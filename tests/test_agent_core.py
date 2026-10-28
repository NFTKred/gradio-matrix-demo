from conftest import load_agent, make_reply, make_thread

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
