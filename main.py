"""Gradio viewer for the Matrix agents demo: starts a run as the host and watches it."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import gradio as gr
from dotenv import load_dotenv
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


def cat_threads(client: MatrixClient, cfg: ViewerConfig, log: list[dict]) -> list[tuple[str, str]]:
    threads = list(client.threads.list(cfg.cat_node_id, limit=20))
    log_call(log, f"client.threads.list({cfg.cat_node_id!r}, limit=20)", threads)
    return [(t.title or (t.text or "")[:60], t.id) for t in threads]


def cat_conversation(client: MatrixClient, thread_id: str, log: list[dict]) -> list[dict]:
    thread = client.threads.get(thread_id)
    log_call(log, f"client.threads.get({thread_id!r})", thread)
    replies = list(client.threads.conversation(thread_id))
    log_call(log, f"client.threads.conversation({thread_id!r})", replies)
    return to_chat(thread, replies, {})


def webhook_status(cfg: ViewerConfig) -> str:
    agents = [("Planner", cfg.planner_id, cfg.planner_token), ("Critic", cfg.critic_id, cfg.critic_token)]
    lines = []
    for name, agent_id, token in agents:
        if not token:
            continue
        try:
            hook = MatrixClient(token).agent_webhooks.get(agent_id)
            lines.append(
                f"{name}: enabled={hook.enabled}, last_status={hook.last_status}, "
                f"failures={hook.failures}, last_error={hook.last_error}"
            )
        except MatrixError as e:
            lines.append(f"{name}: {e}")
    if not lines:
        return "Set MATRIX_PLANNER_TOKEN / MATRIX_CRITIC_TOKEN to see webhook delivery status."
    return "\n\n".join(lines)


EXAMPLE_TASKS = [
    "Plan a weekend trip to Lisbon for two on a modest budget.",
    "Draft a launch plan for a small neighbourhood bakery's first week.",
    "Outline a study schedule for learning Spanish in three months.",
]


def build_ui(cfg: ViewerConfig) -> gr.Blocks:
    host_client: list[MatrixClient] = []

    def host() -> MatrixClient:
        # Created on first use so building the UI makes no network calls.
        if not host_client:
            host_client.append(MatrixClient(cfg.host_token))
        return host_client[0]

    def show_balance() -> str:
        try:
            return f"Host balance: {host_balance(host(), [])}"
        except MatrixError as e:
            return f"Host balance unavailable: {e}"

    def run_task(task: str):
        yield from start_run(task, cfg, host())

    def refresh_cat():
        log: list[dict] = []
        try:
            choices = cat_threads(host(), cfg, log)
        except MatrixError as e:
            return gr.update(choices=[], value=None), f"Couldn't list threads: {e}", _log_json(log)
        return gr.update(choices=choices, value=None), f"{len(choices)} recent threads.", _log_json(log)

    def show_cat(thread_id: str | None):
        if not thread_id:
            return [], "[]"
        log: list[dict] = []
        try:
            return cat_conversation(host(), thread_id, log), _log_json(log)
        except MatrixError as e:
            return [], _log_json(log + [{"call": "error", "result": str(e)}])

    with gr.Blocks(title="Matrix Agents Demo") as demo:
        with gr.Tab("Agents talking"):
            gr.Markdown(
                "# Matrix agents demo\n"
                "Two Claude agents, a planner and a critic, run on Vercel and talk to each other "
                "through Matrix. Matrix is the wire: this page only reads the thread back. The agents' "
                "own SDK calls happen on Vercel, so see their logs there. The panel on the right shows "
                "the calls this viewer makes.\n\n"
                "**Start spends one thread charge from the host account.**"
            )
            balance = gr.Markdown("Host balance: loading...")
            refresh_balance = gr.Button("Refresh balance", size="sm")
            with gr.Row():
                with gr.Column(scale=2):
                    task = gr.Textbox(label="Task for the agents", placeholder=EXAMPLE_TASKS[0])
                    gr.Examples(examples=EXAMPLE_TASKS, inputs=task)
                    start = gr.Button("Start", variant="primary")
                    chatbot = gr.Chatbot(height=500)
                    status = gr.Markdown("")
                with gr.Column(scale=1):
                    gr.Markdown("### SDK calls (this viewer)")
                    call_log = gr.Code(language="json")
                    gr.Markdown("### Agent webhook status")
                    hooks = gr.Markdown(webhook_status(cfg))
                    refresh_hooks = gr.Button("Refresh webhook status", size="sm")

            start.click(lambda: gr.update(interactive=False), None, start).then(
                run_task, task, [chatbot, call_log, status]
            ).then(lambda: gr.update(interactive=True), None, start)
            refresh_hooks.click(lambda: webhook_status(cfg), None, hooks)
            refresh_balance.click(show_balance, None, balance)
            demo.load(show_balance, None, balance)

        with gr.Tab("Cat Matrix (read-only)"):
            gr.Markdown(
                "Another team's cat-roleplaying agents at catmatrix.kred. This tab only reads "
                "(`threads.list`, `threads.get`, `threads.conversation`)."
            )
            refresh_cats = gr.Button("Refresh threads")
            cat_status = gr.Markdown("")
            with gr.Row():
                with gr.Column(scale=2):
                    cat_picker = gr.Dropdown(label="Thread", choices=[])
                    cat_chat = gr.Chatbot(height=500)
                with gr.Column(scale=1):
                    gr.Markdown("### SDK calls (this viewer)")
                    cat_log = gr.Code(language="json")
            refresh_cats.click(refresh_cat, None, [cat_picker, cat_status, cat_log])
            cat_picker.change(show_cat, cat_picker, [cat_chat, cat_log])

    return demo


if __name__ == "__main__":
    load_dotenv()
    build_ui(load_config()).launch()
