import json

import pytest
from fastapi.testclient import TestClient
from vercel.queue import DuplicateIdempotencyKeyError

from conftest import FakeMatrix, load_agent, make_reply, make_thread, sign

SECRET = "whsec_test"
planner = load_agent("planner")


def message_event(node="node_d", delivery="dlv_1"):
    return {
        "event": "message.created",
        "created": 1,
        "delivery": delivery,
        "data": {
            "message": {"author": {"id": "usr_h"}, "created": 1, "id": "thr_1", "type": "thread", "text": "hi"},
            "node": {"id": node},
            "recipient": "agt_p",
            "thread": {"id": "thr_1"},
        },
    }


def ping_event():
    return {"event": "ping", "created": 1, "delivery": "dlv_p", "data": {"agent": "agt_p"}}


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("MATRIX_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("MATRIX_DEMO_NODE_ID", "node_d")
    calls = []

    async def fake_send(topic, payload, **kwargs):
        calls.append((topic, payload, kwargs))

    monkeypatch.setattr(planner.app, "send", fake_send)
    return calls


def post(body: dict, secret=SECRET):
    raw = json.dumps(body).encode()
    return TestClient(planner.app.app).post("/api/webhook", content=raw, headers=sign(raw, secret))


def test_bad_signature_rejected(sent):
    assert post(message_event(), secret="whsec_wrong").status_code == 400
    assert sent == []


def test_ping_acknowledged(sent):
    response = post(ping_event())
    assert response.status_code == 200
    assert sent == []


def test_message_enqueued_with_delivery_key(sent):
    response = post(message_event())
    assert response.status_code == 200
    assert response.json() == {"queued": True}
    topic, payload, kwargs = sent[0]
    assert topic is planner.worker.NOTICES
    assert payload["delivery"] == "dlv_1"
    assert kwargs == {"idempotency_key": "dlv_1"}


def test_other_node_not_enqueued(sent):
    assert post(message_event(node="node_z")).status_code == 200
    assert sent == []


def test_duplicate_enqueue_is_ok(sent, monkeypatch):
    async def duplicate(topic, payload, **kwargs):
        raise DuplicateIdempotencyKeyError("dup")

    monkeypatch.setattr(planner.app, "send", duplicate)
    response = post(message_event())
    assert response.status_code == 200
    assert response.json() == {"queued": True}


def test_process_payload_runs_turn(monkeypatch):
    for key, value in {
        "MATRIX_SELF_ID": "agt_p",
        "MATRIX_HOST_ID": "usr_h",
        "MATRIX_CRITIC_ID": "agt_c",
        "MATRIX_PLANNER_ID": "agt_p",
        "MATRIX_DEMO_NODE_ID": "node_d",
    }.items():
        monkeypatch.setenv(key, value)
    matrix = FakeMatrix()
    matrix.returns["threads.get"] = make_thread()
    matrix.returns["threads.reply"] = make_reply(id="rply_9")
    monkeypatch.setattr(planner.worker, "MatrixClient", lambda: matrix)
    monkeypatch.setattr(planner.worker.anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr(planner.worker, "ask_claude", lambda client, system, text: "ok")
    outcome = planner.worker.process_payload(message_event())
    assert outcome == "replied rply_9"
    (sent,) = [kw for name, kw in matrix.calls if name == "threads.reply"]
    assert sent["idempotency_key"] == "dlv_1"


def test_two_agents_load_in_one_process(tmp_path):
    from conftest import ROOT, load_agent_folder
    import shutil

    copy = tmp_path / "critic"
    shutil.copytree(ROOT / "agents" / "planner", copy, ignore=shutil.ignore_patterns("vendor", "__pycache__"))
    second = load_agent_folder(copy)
    assert second.worker is not planner.worker
    assert second.app.app is not planner.app.app
