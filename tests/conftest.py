import functools
import hashlib
import hmac
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from matrix_kred import models

ROOT = Path(__file__).resolve().parent.parent
AGENT_MODULES = ("core", "role", "worker", "app")


@functools.cache
def load_agent(role: str) -> SimpleNamespace:
    """Load agents/<role>/*.py under their plain names, isolated from other agents.

    Cached: worker.py registers a global queue subscriber, so it may be loaded only once.
    """
    folder = ROOT / "agents" / role
    for name in AGENT_MODULES:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(folder))
    loaded = {}
    try:
        for name in AGENT_MODULES:
            path = folder / f"{name}.py"
            if not path.exists():
                loaded[name] = None
                continue
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    finally:
        for name in AGENT_MODULES:
            sys.modules.pop(name, None)
        sys.path.remove(str(folder))
    return SimpleNamespace(**loaded)


def make_thread(**kw) -> models.ThreadResource:
    kw.setdefault("id", "thr_1")
    kw.setdefault("node", "node_d")
    kw.setdefault("user", "usr_h")
    kw.setdefault("text", "Plan a picnic")
    return models.ThreadResource(**kw)


def make_reply(**kw) -> models.ReplyResource:
    kw.setdefault("id", "rply_1")
    kw.setdefault("user", "usr_p")
    kw.setdefault("text", "reply text")
    return models.ReplyResource(**kw)


class _Resource:
    def __init__(self, owner, prefix):
        self._owner = owner
        self._prefix = prefix

    def __getattr__(self, method):
        name = f"{self._prefix}.{method}"

        def call(*args, **kwargs):
            return self._owner._call(name, args, kwargs)

        return call


class FakeMatrix:
    """Records (method, kwargs) per call; positional args are stored under 'args'.

    Set results with `returns["threads.get"] = value` and failures with
    `raises["threads.reply"] = exc`.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.returns: dict = {}
        self.raises: dict = {}
        self.threads = _Resource(self, "threads")
        self.ledger = _Resource(self, "ledger")
        self.agent_webhooks = _Resource(self, "agent_webhooks")

    def _call(self, name, args, kwargs):
        record = dict(kwargs)
        if args:
            record["args"] = args
            if name in ("threads.get", "threads.conversation", "threads.reply", "agent_webhooks.get"):
                record["id"] = args[0]
            elif name in ("threads.create", "threads.list"):
                record["node_id"] = args[0]
        self.calls.append((name, record))
        if name in self.raises:
            raise self.raises[name]
        result = self.returns.get(name)
        if name == "threads.conversation":
            return iter(result or [])
        return result


def sign(body: bytes, secret: str, t: int | None = None) -> dict[str, str]:
    """Headers Matrix sends with a webhook delivery, signed with `secret`."""
    t = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-Matrix-Signature": f"t={t},v1={digest}",
        "X-Matrix-Event": "message.created",
        "X-Matrix-Delivery": "dlv_1",
        "Content-Type": "application/json",
    }
