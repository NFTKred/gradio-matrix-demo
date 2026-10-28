import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import anthropic
from matrix_kred import MatrixClient, MatrixError, models

import role

MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class AgentConfig:
    self_id: str
    host_id: str
    answers_to: frozenset[str]
    demo_node_id: str
    reply_cap: int


def load_config(env: Mapping[str, str] = os.environ) -> AgentConfig:
    return AgentConfig(
        self_id=env["MATRIX_SELF_ID"],
        host_id=env["MATRIX_HOST_ID"],
        answers_to=frozenset(env[name] for name in role.ANSWERS_TO_ENV),
        demo_node_id=env["MATRIX_DEMO_NODE_ID"],
        reply_cap=int(env.get("REPLY_CAP") or 4),
    )


def _bare(i: str) -> str:
    return i.split("_", 1)[-1]


def same_id(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return _bare(a) == _bare(b)


def _matches(author: str | None, ids) -> bool:
    return any(same_id(author, i) for i in ids)


def skip_reason(
    thread: models.ThreadResource, replies: list[models.ReplyResource], cfg: AgentConfig
) -> str | None:
    # A direct GET returns node=null; the node is checked against the signed event in app.py.
    if thread.node is not None and thread.node != cfg.demo_node_id:
        return "other node"
    if not same_id(thread.user, cfg.host_id):
        return "not a host thread"
    if len(replies) >= cfg.reply_cap:
        return "reply cap reached"
    latest = replies[-1].user if replies else thread.user
    if same_id(latest, cfg.self_id):
        return "already answered"
    if not _matches(latest, cfg.answers_to):
        return "not my turn"
    return None


def transcript(
    thread: models.ThreadResource, replies: list[models.ReplyResource], names: Mapping[str, str]
) -> str:
    def name_of(author: str | None) -> str:
        for known, name in names.items():
            if same_id(author, known):
                return name
        return "Someone"

    blocks = [f"Task: {thread.text}"]
    blocks += [f"{name_of(r.user)}: {r.text}" for r in replies]
    return "\n\n".join(blocks)


def ask_claude(client: anthropic.Anthropic, system: str, text: str) -> str | None:
    response = client.beta.messages.create(
        model=MODEL,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        print(f"claude refused: category={getattr(details, 'category', None)}")
        return None
    return "".join(b.text for b in response.content if b.type == "text")


def take_turn(
    matrix: MatrixClient,
    ask: Callable[[str, str], str | None],
    cfg: AgentConfig,
    thread_id: str,
    delivery_id: str,
    names: Mapping[str, str],
) -> str:
    thread = matrix.threads.get(thread_id)
    replies = list(matrix.threads.conversation(thread_id))
    reason = skip_reason(thread, replies, cfg)
    if reason:
        return f"skipped: {reason}"
    text = ask(role.SYSTEM_PROMPT, transcript(thread, replies, names))
    if text is None:
        return "refused"
    if not text.strip():
        return "empty"
    # From here on Claude has been paid for: never raise, or the queue retries and pays again.
    try:
        reply = matrix.threads.reply(thread_id, text=text, idempotency_key=delivery_id)
    except MatrixError as exc:
        return f"failed: {getattr(exc, 'code', None) or exc}"
    return f"replied {reply.id}"


def agent_names(cfg_env: Mapping[str, str]) -> dict[str, str]:
    labels = {"MATRIX_HOST_ID": "Host", "MATRIX_PLANNER_ID": "Planner", "MATRIX_CRITIC_ID": "Critic"}
    return {cfg_env[key]: label for key, label in labels.items() if cfg_env.get(key)}
