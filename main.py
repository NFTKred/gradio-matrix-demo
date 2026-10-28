"""Gradio viewer for the Matrix agents demo: starts a run as the host and watches it."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from matrix_kred import MatrixClient
from matrix_kred.errors import MatrixError, PaymentRequiredError

POLL_SECONDS = 3
RUN_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class ViewerConfig:
    host_token: str
    host_id: str
    planner_id: str
    critic_id: str
    demo_node_id: str
    cat_node_id: str
    planner_token: str | None
    critic_token: str | None
    reply_cap: int


def load_config(env: Mapping[str, str] = os.environ) -> ViewerConfig:
    return ViewerConfig(
        host_token=env["MATRIX_HOST_TOKEN"],
        host_id=env.get("MATRIX_HOST_ID", ""),
        planner_id=env["MATRIX_PLANNER_ID"],
        critic_id=env["MATRIX_CRITIC_ID"],
        demo_node_id=env["MATRIX_DEMO_NODE_ID"],
        cat_node_id=env["MATRIX_CAT_NODE_ID"],
        planner_token=env.get("MATRIX_PLANNER_TOKEN") or None,
        critic_token=env.get("MATRIX_CRITIC_TOKEN") or None,
        reply_cap=int(env.get("REPLY_CAP") or 4),
    )


def names_for(cfg: ViewerConfig) -> dict[str, str]:
    return {cfg.host_id: "Host", cfg.planner_id: "Planner", cfg.critic_id: "Critic"}


def _bare(user_id: str) -> str:
    return user_id.split("_", 1)[-1]


def _same_id(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and _bare(a) == _bare(b)


def _dump(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def log_call(log: list[dict], code: str, result: Any) -> None:
    log.append({"call": code, "result": _dump(result)})


def _label(message, names: Mapping[str, str], fallback_names: bool) -> str:
    for known_id, name in names.items():
        if _same_id(message.user, known_id):
            return name
    if fallback_names and message.author is not None and message.author.name:
        return message.author.name
    return "Someone"


def to_chat(thread, replies, names: Mapping[str, str], fallback_names: bool = True) -> list[dict]:
    chat = [{"role": "user", "content": thread.text}]
    for reply in replies:
        label = _label(reply, names, fallback_names)
        chat.append({"role": "assistant", "content": f"**{label}:** {reply.text}"})
    return chat


def host_balance(host: MatrixClient, log: list[dict]) -> str:
    balance = host.ledger.balance()
    log_call(log, "host.ledger.balance()", balance)
    return f"{balance.amount_decimal} {balance.currency}"


def _log_json(log: list[dict]) -> str:
    return json.dumps(log, indent=2)


def start_run(
    task: str,
    cfg: ViewerConfig,
    host: MatrixClient,
    *,
    sleep=time.sleep,
    clock=time.monotonic,
) -> Iterator[tuple[list[dict], str, str]]:
    task = (task or "").strip()
    if not task:
        yield [], "[]", "Enter a task first."
        return

    log: list[dict] = []
    participants = [cfg.planner_id, cfg.critic_id]
    code = (
        f"host.threads.create({cfg.demo_node_id!r}, title={task[:80]!r}, text={task!r}, "
        f"participants={participants!r}, idempotency_key=<uuid>)"
    )
    try:
        thread = host.threads.create(
            cfg.demo_node_id,
            title=task[:80],
            text=task,
            participants=participants,
            idempotency_key=uuid.uuid4().hex,
        )
    except PaymentRequiredError as e:
        log_call(log, code, str(e))
        yield [], _log_json(log), f"Host can't pay for a thread: {e.code} ({e.message}). Fund the host and try again."
        return
    except MatrixError as e:
        message = getattr(e, "message", None) or str(e)
        log_call(log, code, str(e))
        yield [], _log_json(log), f"Couldn't start the run: {message}"
        return
    log_call(log, code, thread)

    names = names_for(cfg)
    started = clock()
    count = -1
    while True:
        try:
            current = host.threads.get(thread.id)
            replies = list(host.threads.conversation(thread.id))
        except MatrixError as e:
            yield to_chat(thread, [], names), _log_json(log), f"Lost contact with Matrix: {e}"
            return
        if len(replies) != count:
            count = len(replies)
            log_call(log, f"host.threads.get({thread.id!r})", current)
            log_call(log, f"host.threads.conversation({thread.id!r})", replies)
        chat = to_chat(current, replies, names)
        if count >= cfg.reply_cap:
            yield chat, _log_json(log), f"Done: {count} replies."
            return
        if clock() - started > RUN_TIMEOUT_SECONDS:
            yield chat, _log_json(log), (
                f"Timed out after {RUN_TIMEOUT_SECONDS}s with {count} replies. "
                "Check the agents' webhook status."
            )
            return
        yield chat, _log_json(log), f"Waiting for the agents... {count} of {cfg.reply_cap} replies."
        sleep(POLL_SECONDS)
