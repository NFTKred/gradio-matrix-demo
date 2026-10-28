import os
from collections.abc import Mapping
from dataclasses import dataclass

from matrix_kred import models

import role


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
    if thread.node != cfg.demo_node_id:
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
