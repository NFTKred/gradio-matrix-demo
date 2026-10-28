import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from matrix_kred import WebhookVerificationError, models, webhooks
from vercel.queue import DuplicateIdempotencyKeyError, send

import role
from worker import NOTICES

app = FastAPI()


@app.get("/")
def root():
    return {"agent": role.ROLE_NAME}


@app.post("/api/webhook")
async def webhook(request: Request):
    body = await request.body()
    try:
        event = webhooks.construct_event(body, request.headers, os.environ["MATRIX_WEBHOOK_SECRET"])
    except WebhookVerificationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if isinstance(event, models.WebhookPingEvent):
        return {"ok": True}
    if event.data.node.id != os.environ["MATRIX_DEMO_NODE_ID"]:
        return {"ignored": "other node"}
    try:
        await send(NOTICES, event.model_dump(mode="json"), idempotency_key=event.delivery)
    except DuplicateIdempotencyKeyError:
        pass
    return {"queued": True}
