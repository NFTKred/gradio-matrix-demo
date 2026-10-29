import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("setup_demo", ROOT / "scripts" / "setup_demo.py")
setup_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_demo)


class FakeClient:
    """Records calls; checks each agent's token is on disk before the next call."""

    def __init__(self, env_file: Path):
        self.calls: list[str] = []
        self.env_file = env_file
        self._pending_token: str | None = None
        outer = self
        self.nodes = SimpleNamespace(
            create=lambda **kw: outer._call("nodes.create", SimpleNamespace(id="node_d", path="/d", owner="usr_me")),
            update_governance=lambda node_id, **kw: outer._call("nodes.update_governance", None),
            get=lambda node_id: outer._call(
                "nodes.get", SimpleNamespace(governance=SimpleNamespace(costs={"thread": "1"}))
            ),
        )
        self.agents = SimpleNamespace(create=self._create_agent)
        self.allowances = SimpleNamespace(
            set=lambda child_id, **kw: outer._call("allowances.set", None),
            get=lambda child_id: outer._call("allowances.get", SimpleNamespace(model_dump=lambda **kw: {})),
        )

    def _call(self, name, result):
        if self._pending_token:
            assert self._pending_token in dotenv_values(self.env_file).values(), "token not saved first"
            self._pending_token = None
        self.calls.append(name)
        return result

    def _create_agent(self, *, name, address, idempotency_key):
        result = self._call("agents.create", SimpleNamespace(id=f"agt_{address}", token=f"tok_{address}"))
        self._pending_token = result.token
        return result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MATRIX_OWNER_TOKEN=owner\n")
    monkeypatch.delenv("MATRIX_OWNER_TOKEN", raising=False)
    return path


def run(env_file, monkeypatch, *extra):
    client = FakeClient(env_file)
    monkeypatch.setattr(setup_demo, "MatrixClient", lambda token: client)
    setup_demo.main(["--domain", "me.kred", "--budget", "50", *extra], env_file=env_file)
    return client


def test_dry_run_makes_no_calls(env_file, monkeypatch):
    assert run(env_file, monkeypatch).calls == []
    assert set(dotenv_values(env_file)) == {"MATRIX_OWNER_TOKEN"}


def test_apply_records_ids_and_tokens(env_file, monkeypatch):
    client = run(env_file, monkeypatch, "--apply")
    values = dotenv_values(env_file)
    assert values["MATRIX_DEMO_NODE_ID"] == "node_d"
    assert values["MATRIX_HOST_ID"] == "agt_matrix-agents-demo-host"
    assert values["MATRIX_HOST_TOKEN"] == "tok_matrix-agents-demo-host"
    assert values["MATRIX_CRITIC_TOKEN"] == "tok_matrix-agents-demo-critic"
    assert values["REPLY_CAP"] == "4"
    assert client.calls.count("agents.create") == 3
    assert "allowances.set" in client.calls


def test_rerun_skips_recorded_node_and_agents(env_file, monkeypatch):
    run(env_file, monkeypatch, "--apply")
    client = run(env_file, monkeypatch, "--apply")
    assert "nodes.create" not in client.calls
    assert "agents.create" not in client.calls


def test_missing_owner_token(tmp_path, monkeypatch):
    monkeypatch.delenv("MATRIX_OWNER_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        setup_demo.main(["--domain", "me.kred", "--budget", "50"], env_file=tmp_path / ".env")
