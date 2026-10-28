import asyncio
import os
from typing import Any

import anthropic
from matrix_kred import MatrixClient, models
from vercel.queue import Message, Topic, subscribe

from core import agent_names, ask_claude, load_config, take_turn

NOTICES = Topic[dict[str, Any]]("matrix-notices")


def process_payload(payload: dict[str, Any]) -> str:
    event = models.MessageCreatedEvent.model_validate(payload)
    cfg = load_config()
    client = anthropic.Anthropic()
    outcome = take_turn(
        MatrixClient(),
        lambda system, text: ask_claude(client, system, text),
        cfg,
        thread_id=event.data.thread.id,
        delivery_id=event.delivery,
        names=agent_names(os.environ),
    )
    print(f"delivery {event.delivery}: {outcome}")
    return outcome


@subscribe(topic=NOTICES, max_attempts=3, retry_after=60, max_concurrency=1)
async def handle_notice(message: Message[dict[str, Any]]) -> None:
    await asyncio.to_thread(process_payload, message.payload)
