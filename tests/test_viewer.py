import itertools

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
