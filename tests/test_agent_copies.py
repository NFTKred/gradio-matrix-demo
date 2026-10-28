from conftest import ROOT, FakeMatrix, load_agent, make_reply, make_thread

SHARED_FILES = ("core.py", "app.py", "worker.py")

CRITIC_ENV = {
    "MATRIX_SELF_ID": "agt_c", "MATRIX_HOST_ID": "agt_h", "MATRIX_PLANNER_ID": "agt_p",
    "MATRIX_DEMO_NODE_ID": "node_d", "REPLY_CAP": "4",
}


def test_shared_files_identical():
    planner_dir = ROOT / "agents" / "planner"
    critic_dir = ROOT / "agents" / "critic"
    for name in SHARED_FILES:
        assert (critic_dir / name).read_bytes() == (planner_dir / name).read_bytes(), name


def test_critic_role_answers_planner_only():
    critic = load_agent("critic")
    cfg = critic.core.load_config(CRITIC_ENV)
    assert cfg.answers_to == frozenset({"agt_p"})


def test_critic_turn_after_planner():
    critic = load_agent("critic")
    cfg = critic.core.load_config(CRITIC_ENV)
    fake = FakeMatrix()
    fake.returns["threads.get"] = make_thread(user="agt_h")
    fake.returns["threads.conversation"] = [make_reply(user="agt_p", text="Buy bread")]
    fake.returns["threads.reply"] = make_reply(id="rply_new")
    seen = []

    def ask(system, text):
        seen.append((system, text))
        return "Weak point: no budget"

    result = critic.core.take_turn(fake, ask, cfg, "thr_1", "dlv_1", {"agt_p": "Planner"})
    assert result == "replied rply_new"
    assert seen[0][0] == critic.role.SYSTEM_PROMPT
    assert "Planner: Buy bread" in seen[0][1]
    (sent,) = [kw for name, kw in fake.calls if name == "threads.reply"]
    assert sent["text"] == "Weak point: no budget" and sent["idempotency_key"] == "dlv_1"
