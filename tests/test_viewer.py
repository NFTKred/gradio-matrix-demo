import itertools

import gradio as gr

from matrix_kred import errors, models

import main
from conftest import FakeMatrix, make_reply, make_thread

CFG = main.ViewerConfig(
    host_token="tok",
    host_id="usr_h",
    planner_id="usr_p",
    critic_id="agt_c",
    demo_node_id="node_d",
    cat_node_id="node_c",
    planner_token=None,
    critic_token=None,
    reply_cap=4,
)


def clock_from(*values):
    it = iter(values)
    return lambda: next(it)


def ticks():
    return itertools.count(0, 10).__next__


class SequencedMatrix(FakeMatrix):
    """threads.conversation returns the next list in `sequence` (last one repeats)."""

    def __init__(self, sequence):
        super().__init__()
        self.sequence = list(sequence)

    def _call(self, name, args, kwargs):
        if name == "threads.conversation":
            self.calls.append((name, {"id": args[0]}))
            current = self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]
            return iter(current)
        return super()._call(name, args, kwargs)


def run(task, fake, clock=None):
    return list(main.start_run(task, CFG, fake, sleep=lambda s: None, clock=clock or ticks()))


def test_start_run_creates_one_thread_with_participants():
    fake = FakeMatrix()
    fake.returns["threads.create"] = make_thread()
    fake.returns["threads.get"] = make_thread()
    fake.returns["threads.conversation"] = [make_reply() for _ in range(4)]
    run("Plan a picnic", fake)
    creates = [kw for name, kw in fake.calls if name == "threads.create"]
    assert len(creates) == 1
    assert creates[0]["node_id"] == "node_d"
    assert creates[0]["participants"] == ["usr_p", "agt_c"]
    assert creates[0]["text"] == "Plan a picnic"
    assert creates[0]["idempotency_key"]


def test_start_run_stops_at_cap():
    fake = SequencedMatrix([[], [make_reply(id=f"r{i}") for i in range(4)]])
    fake.returns["threads.create"] = make_thread()
    fake.returns["threads.get"] = make_thread()
    steps = run("Plan a picnic", fake)
    chat, log_json, status = steps[-1]
    assert status == "Done: 4 replies."
    assert len(chat) == 5
    assert "threads.create" in log_json


def test_start_run_times_out():
    fake = SequencedMatrix([[]])
    fake.returns["threads.create"] = make_thread()
    fake.returns["threads.get"] = make_thread()
    steps = run("Plan a picnic", fake, clock=clock_from(0, 10, 200, 301, 400))
    assert "webhook" in steps[-1][2]
    assert len(steps[-1][0]) == 1


def test_start_run_reports_insufficient_balance():
    fake = FakeMatrix()
    fake.raises["threads.create"] = errors.PaymentRequiredError(
        status=402, type="payment_required", code="insufficient_balance", message="Not enough XP"
    )
    steps = run("Plan a picnic", fake)
    assert len(steps) == 1
    assert "insufficient_balance" in steps[0][2]
    assert [name for name, _ in fake.calls] == ["threads.create"]


def test_start_run_reports_other_matrix_errors():
    fake = FakeMatrix()
    fake.raises["threads.create"] = errors.MatrixError("boom")
    steps = run("Plan a picnic", fake)
    assert len(steps) == 1
    assert "boom" in steps[0][2]
    assert len(fake.calls) == 1


def test_start_run_lost_contact_keeps_shown_replies():
    class Flaky(SequencedMatrix):
        def _call(self, name, args, kwargs):
            if name == "threads.get" and any(n == "threads.conversation" for n, _ in self.calls):
                if len([1 for n, _ in self.calls if n == "threads.get"]) >= 1:
                    raise errors.MatrixError("down")
            return super()._call(name, args, kwargs)

    fake = Flaky([[make_reply(text="first")]])
    fake.returns["threads.create"] = make_thread()
    fake.returns["threads.get"] = make_thread()
    steps = run("Plan a picnic", fake)
    chat, _, status = steps[-1]
    assert status.startswith("Lost contact")
    assert "first" in chat[-1]["content"]


def test_to_chat_none_text_is_blank():
    chat = main.to_chat(make_thread(text=None), [make_reply(text=None)], {})
    assert chat[0]["content"] == ""
    assert "None" not in chat[1]["content"]


def test_start_run_requires_task():
    fake = FakeMatrix()
    steps = run("  ", fake)
    assert len(steps) == 1
    assert steps[0][2] == "Enter a task first."
    assert fake.calls == []


def test_to_chat_labels_authors():
    names = main.names_for(CFG)
    replies = [make_reply(user="usr_p", text="a"), make_reply(user="agt_c", text="b")]
    chat = main.to_chat(make_thread(), replies, names)
    assert chat[0] == {"role": "user", "content": "Plan a picnic"}
    assert chat[1] == {"role": "assistant", "content": "**Planner:** a"}
    assert chat[2] == {"role": "assistant", "content": "**Critic:** b"}


def test_to_chat_fallback_names():
    names = main.names_for(CFG)
    reply = make_reply(user="usr_x", text="hi", author=models.MessageAuthor(name="Whiskers"))
    anon = make_reply(user="usr_y", text="yo")
    assert main.to_chat(make_thread(), [reply], names)[1]["content"] == "**Whiskers:** hi"
    assert main.to_chat(make_thread(), [anon], names)[1]["content"] == "**Someone:** yo"
    strict = main.to_chat(make_thread(), [reply], names, fallback_names=False)
    assert strict[1]["content"] == "**Someone:** hi"


def test_host_balance_and_log():
    fake = FakeMatrix()
    fake.returns["ledger.balance"] = models.BalanceResource(amount_decimal="12.5", currency="XP")
    log = []
    assert main.host_balance(fake, log) == "12.5 XP"
    assert log[0]["call"] == "host.ledger.balance()"
    assert log[0]["result"]["amount_decimal"] == "12.5"


def test_load_config_reads_env_and_defaults():
    cfg = main.load_config(
        {
            "MATRIX_HOST_TOKEN": "t",
            "MATRIX_HOST_ID": "usr_h",
            "MATRIX_PLANNER_ID": "usr_p",
            "MATRIX_CRITIC_ID": "agt_c",
            "MATRIX_DEMO_NODE_ID": "node_d",
            "MATRIX_CAT_NODE_ID": "node_c",
        }
    )
    assert cfg.reply_cap == 4
    assert cfg.planner_token is None


def test_missing_config_lists_required_names():
    assert main.missing_config({"MATRIX_HOST_TOKEN": "t", "MATRIX_PLANNER_ID": ""}) == [
        "MATRIX_PLANNER_ID",
        "MATRIX_CRITIC_ID",
        "MATRIX_DEMO_NODE_ID",
    ]


def test_cat_node_is_optional():
    cfg = main.load_config(
        {"MATRIX_HOST_TOKEN": "t", "MATRIX_PLANNER_ID": "p", "MATRIX_CRITIC_ID": "c", "MATRIX_DEMO_NODE_ID": "d"}
    )
    assert cfg.cat_node_id == ""
    assert isinstance(main.build_ui(cfg), gr.Blocks)


def test_cat_threads_without_node_makes_no_calls():
    fake = FakeMatrix()
    cfg = main.ViewerConfig(**{**CFG.__dict__, "cat_node_id": ""})
    assert main.cat_threads(fake, cfg, []) == []
    assert fake.calls == []


def test_not_configured_page_builds():
    assert isinstance(main.not_configured_ui(["MATRIX_HOST_TOKEN"]), gr.Blocks)


def test_cat_view_is_read_only():
    fake = FakeMatrix()
    fake.returns["threads.list"] = [make_thread(id="thr_c")]
    fake.returns["threads.get"] = make_thread(id="thr_c")
    fake.returns["threads.conversation"] = [make_reply(user="usr_cat", author=models.MessageAuthor(name="Tom"))]
    log = []
    choices = main.cat_threads(fake, CFG, log)
    chat = main.cat_conversation(fake, choices[0][1], log)
    assert {name for name, _ in fake.calls} == {"threads.list", "threads.get", "threads.conversation"}
    assert fake.calls[0][1]["node_id"] == "node_c"
    assert chat[1]["content"] == "**Tom:** reply text"
    assert [entry["call"].split("(")[0] for entry in log] == [
        "client.threads.list",
        "client.threads.get",
        "client.threads.conversation",
    ]


def test_cat_threads_labels():
    fake = FakeMatrix()
    long_text = "x" * 100
    fake.returns["threads.list"] = [
        make_thread(id="thr_a", title="Named", text="ignored"),
        make_thread(id="thr_b", text=long_text),
    ]
    choices = main.cat_threads(fake, CFG, [])
    assert choices == [("Named", "thr_a"), ("x" * 60, "thr_b")]


def test_webhook_status_without_tokens():
    assert main.webhook_status(CFG).startswith("Set MATRIX_PLANNER_TOKEN / MATRIX_CRITIC_TOKEN")


def test_webhook_status_lines(monkeypatch):
    fake = FakeMatrix()
    fake.returns["agent_webhooks.get"] = models.AgentWebhook(
        enabled=True, last_status=200, failures=0, last_error=None, last_attempt=1700000000
    )
    monkeypatch.setattr(main, "MatrixClient", lambda token: fake)
    cfg = main.ViewerConfig(**{**CFG.__dict__, "planner_token": "p"})
    assert main.webhook_status(cfg) == (
        "Planner: enabled=True, last_status=200, failures=0, last_error=None, last_attempt=1700000000"
    )
    fake.raises["agent_webhooks.get"] = errors.MatrixError("nope")
    assert main.webhook_status(cfg) == "Planner: nope"


def test_build_ui_constructs(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network client created during build_ui")

    monkeypatch.setattr(main, "MatrixClient", no_network)
    assert isinstance(main.build_ui(CFG), gr.Blocks)


def test_build_ui_makes_no_calls_with_tokens_configured(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network client created during build_ui")

    monkeypatch.setattr(main, "MatrixClient", no_network)
    cfg = main.ViewerConfig(**{**CFG.__dict__, "planner_token": "p", "critic_token": "c"})
    assert isinstance(main.build_ui(cfg), gr.Blocks)
