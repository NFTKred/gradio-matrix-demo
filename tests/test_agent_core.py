from types import SimpleNamespace

import matrix_kred

from conftest import FakeMatrix, load_agent, make_reply, make_thread

planner = load_agent("planner")
core = planner.core

CFG = core.AgentConfig(
    self_id="agt_p", host_id="agt_h", answers_to=frozenset({"agt_h", "agt_c"}),
    demo_node_id="node_d", reply_cap=4,
)
CRITIC_CFG = core.AgentConfig(
    self_id="agt_c", host_id="agt_h", answers_to=frozenset({"agt_p"}),
    demo_node_id="node_d", reply_cap=4,
)


def test_same_id_ignores_prefix():
    assert core.same_id("agt_abc", "usr_abc")
    assert not core.same_id("agt_abc", "agt_xyz")
    assert not core.same_id(None, "agt_abc")


def test_responds_to_host_opening_post():
    assert core.skip_reason(make_thread(user="usr_h"), [], CFG) is None


def test_ignores_other_node():
    assert core.skip_reason(make_thread(node="node_x"), [], CFG) == "other node"


def test_ignores_non_host_thread():
    assert core.skip_reason(make_thread(user="agt_x"), [], CFG) == "not a host thread"


def test_stops_at_reply_cap():
    replies = [make_reply(id=f"r{i}", user="agt_c") for i in range(4)]
    assert core.skip_reason(make_thread(), replies, CFG) == "reply cap reached"


def test_skip_when_latest_is_own():
    replies = [make_reply(user="agt_c"), make_reply(user="usr_p")]
    assert core.skip_reason(make_thread(), replies, CFG) == "already answered"


def test_responds_to_critic_reply():
    replies = [make_reply(user="usr_p"), make_reply(user="agt_c")]
    assert core.skip_reason(make_thread(), replies, CFG) is None


def test_critic_ignores_opening_post():
    assert core.skip_reason(make_thread(), [], CRITIC_CFG) == "not my turn"


def test_transcript_orders_and_labels():
    replies = [make_reply(user="usr_p", text="Buy bread"), make_reply(user="agt_c", text="Weather?")]
    names = {"agt_p": "Planner", "agt_c": "Critic"}
    assert core.transcript(make_thread(), replies, names) == (
        "Task: Plan a picnic\n\nPlanner: Buy bread\n\nCritic: Weather?"
    )


def test_transcript_unknown_author():
    out = core.transcript(make_thread(), [make_reply(user="usr_z", text="hi")], {})
    assert out.endswith("Someone: hi")


def test_load_config_reads_env():
    env = {
        "MATRIX_SELF_ID": "agt_p", "MATRIX_HOST_ID": "agt_h", "MATRIX_CRITIC_ID": "agt_c",
        "MATRIX_DEMO_NODE_ID": "node_d", "REPLY_CAP": "6",
    }
    cfg = core.load_config(env)
    assert cfg.self_id == "agt_p" and cfg.host_id == "agt_h"
    assert cfg.answers_to == frozenset({"agt_h", "agt_c"})
    assert cfg.demo_node_id == "node_d" and cfg.reply_cap == 6
    del env["REPLY_CAP"]
    assert core.load_config(env).reply_cap == 4


def _fake_matrix(thread, replies=()):
    fake = FakeMatrix()
    fake.returns["threads.get"] = thread
    fake.returns["threads.conversation"] = list(replies)
    fake.returns["threads.reply"] = make_reply(id="rply_new")
    return fake


def _replies_sent(fake):
    return [kw for name, kw in fake.calls if name == "threads.reply"]


def test_take_turn_replies_with_delivery_key():
    fake = _fake_matrix(make_thread(user="agt_h"))
    seen = []

    def ask(system, text):
        seen.append((system, text))
        return "A plan"

    result = core.take_turn(fake, ask, CFG, "thr_1", "dlv_1", {"agt_h": "Host"})
    assert result == "replied rply_new"
    (sent,) = _replies_sent(fake)
    assert sent["id"] == "thr_1"
    assert sent["text"] == "A plan" and sent["idempotency_key"] == "dlv_1"
    assert seen[0][0] == planner.role.SYSTEM_PROMPT
    assert "Plan a picnic" in seen[0][1]


def test_take_turn_skips_without_calling_claude():
    fake = _fake_matrix(make_thread(user="agt_x"))
    calls = []
    result = core.take_turn(fake, lambda s, t: calls.append(t), CFG, "thr_1", "dlv_1", {})
    assert result == "skipped: not a host thread"
    assert calls == [] and _replies_sent(fake) == []


def test_take_turn_refusal_posts_nothing():
    fake = _fake_matrix(make_thread(user="agt_h"))
    assert core.take_turn(fake, lambda s, t: None, CFG, "thr_1", "dlv_1", {}) == "refused"
    assert _replies_sent(fake) == []


def test_take_turn_reply_forbidden():
    fake = _fake_matrix(make_thread(user="agt_h"))
    fake.raises["threads.reply"] = matrix_kred.PermissionDeniedError(
        status=403, type="permission_denied", code="forbidden", message="no"
    )
    result = core.take_turn(fake, lambda s, t: "A plan", CFG, "thr_1", "dlv_1", {})
    assert result == "failed: forbidden"


def test_ask_claude_refusal_returns_none():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="refusal", content=[], stop_details=None)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert core.ask_claude(client, "sys", "hello") is None
    assert captured["model"] == "claude-sonnet-5-5"
    assert captured["fallbacks"] == "default"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["max_tokens"] == 4000
    assert captured["system"] == "sys"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]


def test_ask_claude_joins_text_blocks():
    blocks = [SimpleNamespace(type="text", text="a"), SimpleNamespace(type="thinking"),
              SimpleNamespace(type="text", text="b")]
    create = lambda **kw: SimpleNamespace(stop_reason="end_turn", content=blocks)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert core.ask_claude(client, "s", "t") == "ab"


def test_agent_names_maps_set_ids_only():
    env = {"MATRIX_HOST_ID": "agt_h", "MATRIX_CRITIC_ID": "agt_c"}
    assert core.agent_names(env) == {"agt_h": "Host", "agt_c": "Critic"}
