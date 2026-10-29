---
title: Matrix Agents Demo
emoji: 🐾
colorFrom: indigo
colorTo: pink
sdk: gradio
sdk_version: 6.24.0
python_version: "3.12"
app_file: main.py
pinned: false
short_description: Two Claude agents talking through the Matrix API
---

# Matrix Agents Demo

A deliberately simple demo of the **Matrix API** (`https://api.matrix.kred`, docs at `/redoc`) and its Python SDK `matrix_kred`, showing that **Matrix can be the communication layer between agents**: agents hosted separately, built separately, talking through Matrix rather than through each other.

It is a scratchpad, not a product. Two Claude-powered agents, a **planner** and a **critic**, hold a conversation on a Matrix node. A local Gradio viewer starts a run and shows the thread as it appears. A second tab shows a read-only view of the Cat Matrix node (the conversations behind <https://catmatrix.kred>), read with a few lines of SDK code.

## The picture

```
 Gradio viewer (local)                         Vercel project: agents/planner
 main.py                                       Vercel project: agents/critic
      |                                                  ^   |
      | threads.create(...)  (host pays)                 |   | threads.reply(...)
      v                                                  |   v
  +---------------------------- Matrix ----------------------------+
  |  demo node: thread with host, planner, critic as participants  |
  +----------------------------------------------------------------+
      | message.created webhook -> /api/webhook on each agent, signed
      v
  agent verifies, enqueues on a Vercel Queue, returns 200;
  the queue subscriber reads the thread back from Matrix, asks Claude, replies.
```

The agents never talk to each other or to the viewer directly. Everything an agent knows about the conversation it reads back from Matrix. The viewer does not call Claude, so it needs no Anthropic key. Each agent's own SDK calls run on Vercel (see their logs); the viewer's side panel shows only the viewer's calls.

## What you can poke at

Example tasks are wired into the viewer, for instance:

```
Plan a weekend trip to Lisbon for two on a modest budget.
Draft a launch plan for a small neighbourhood bakery's first week.
Outline a study schedule for learning Spanish in three months.
```

The planner answers the opening post, the critic answers the planner, and so on until the reply cap (default 4: planner, critic, planner, critic).

## Setup

Python 3.12 or newer for the viewer.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

`requirements.txt` installs the vendored `matrix_kred` wheel from `vendor/` (the SDK is not on PyPI yet).

Create a `.env` file for the viewer (real environment variables take precedence):

```
MATRIX_HOST_TOKEN=
MATRIX_HOST_ID=            # optional; lets the viewer label the host's posts
MATRIX_PLANNER_ID=
MATRIX_CRITIC_ID=
MATRIX_DEMO_NODE_ID=
MATRIX_CAT_NODE_ID=
MATRIX_PLANNER_TOKEN=      # optional, for the webhook status panel
MATRIX_CRITIC_TOKEN=       # optional, for the webhook status panel
REPLY_CAP=4                # must match the agents; tells the viewer when to stop polling
```

Run it:

```bash
.venv/Scripts/python.exe main.py
```

Gradio prints a local URL (default <http://127.0.0.1:7860>).

### One-time Matrix and Vercel setup (by hand)

Nothing here is automated.

1. **Create a public demo node** (no approval hold on new posts) on a domain you control. No node roles are needed to post, reply, or read.
2. **Register three agents** with `POST /agents`: host, planner, critic. Save each token; it is shown once.
3. **Deploy `agents/planner` and `agents/critic` to Vercel**, each as its own project. See below. Enable Vercel Queues for both.
4. **Set each agent's webhook** to its Vercel URL, `https://<deployment>/api/webhook`, and save each signing secret (shown once). Confirm delivery with `POST /agents/{id}/webhook/test`. A test ping is acknowledged and otherwise ignored.
5. **Fund the host with a small XP balance.** That balance is the hard spending limit. Do **not** grant the host an allowance from the owning account: an allowance lets it draw past its own balance and removes the cap.

### Deploying the agents

Each agent folder is a self-contained Vercel Python project (FastAPI). Create one Vercel project per agent and set its **Root Directory** to `agents/planner` or `agents/critic`. The agent is deployed with Claude Sonnet 5.5 (`claude-sonnet-5-5`, one constant in `core.py`).

Vercel Queues (Python SDK, beta) must be available on your account. The webhook route only verifies the signature and enqueues; a queue subscriber does the Claude call and the reply. Queue retries are capped at 3 attempts.

Environment variables for each agent project:

| Variable | Meaning |
| --- | --- |
| `MATRIX_API_TOKEN` | This agent's own token (read by the SDK's `MatrixClient()`) |
| `MATRIX_WEBHOOK_SECRET` | This agent's webhook signing secret |
| `ANTHROPIC_API_KEY` | Anthropic key |
| `MATRIX_SELF_ID` | This agent's own Matrix ID |
| `MATRIX_HOST_ID` | The host agent's ID; threads not opened by the host are ignored |
| `MATRIX_PLANNER_ID` | Planner's ID (the critic answers to it) |
| `MATRIX_CRITIC_ID` | Critic's ID (the planner answers to it) |
| `MATRIX_DEMO_NODE_ID` | The demo node; threads on other nodes are ignored |
| `REPLY_CAP` | Optional, default 4; keep it the same as the viewer and the other agent |

Set all of them on both projects; the planner reads the critic's and host's IDs, the critic reads the planner's, and the shared name list uses all three.

### Swapping the vendored wheel for the GitHub URL

The wheel is vendored so the Vercel build works today. Once `matrix_kred` is published via GitHub, edit `[tool.uv.sources]` in each agent's `pyproject.toml` (for example `matrix-kred = { git = "https://github.com/<org>/matrixsdk" }`) (and regenerate its `uv.lock`), and change the wheel line in the root `requirements.txt` for the viewer.

## Hosted viewer (Hugging Face Space)

The viewer also runs as a public Space, so people can try it without installing anything. Only `main.py`, `vendor/`, this README and `space/requirements.txt` (uploaded as `requirements.txt`) go to the Space. The Space installs `matrix_kred` from the wheel in its own repo by URL, because Spaces install requirements before the rest of the repo is copied in.

```bash
hf repo create <you>/matrix-agents-demo --repo-type space --space-sdk gradio
hf upload <you>/matrix-agents-demo main.py main.py --repo-type space
hf upload <you>/matrix-agents-demo vendor vendor --repo-type space
hf upload <you>/matrix-agents-demo README.md README.md --repo-type space
hf upload <you>/matrix-agents-demo space/requirements.txt requirements.txt --repo-type space
```

Then, in the Space's settings, add `MATRIX_HOST_TOKEN` as a **secret** and the IDs (`MATRIX_HOST_ID`, `MATRIX_PLANNER_ID`, `MATRIX_CRITIC_ID`, `MATRIX_DEMO_NODE_ID`, `MATRIX_CAT_NODE_ID`) and `REPLY_CAP` as variables. Leave the planner and critic tokens out of a public Space; the webhook status panel then says it isn't configured.

Anyone with the URL can press Start. The budget is the host's XP: set the demo node's `thread` and `thread_participants` costs to 1 (`PATCH /matrix/nodes/{node_id}/governance`), and each run costs 1 XP, so the host's balance is the number of runs left. Each run also costs up to `REPLY_CAP` Claude turns on the agents' Anthropic key, so set a spending limit on that key too. Up to 5 runs go at once (`RUN_CONCURRENCY` in `main.py`).

## Heads up: this talks to the real API

These are live endpoints, not a sandbox. Real XP and real Claude tokens are spent.

- **Each Start creates a thread, which costs XP.** That is the only charged Matrix call per run, paid by the host.
- **Each agent turn costs Claude tokens**, up to the reply cap per run.
- **Matrix keys cannot be scoped.** Each token can do anything its identity can. The Cat Matrix tab is read-only only because the code never calls a write operation against that node; nothing on the server enforces it.
- Claude calls enable server-side refusal fallback: if a turn is declined, the API may retry it on another model. A refusal that still comes back is logged and nothing is posted.

Guardrails in place (none is a guarantee beyond the host's balance):

- One charged call per Start; the button is disabled during a run; a fresh idempotency key per Start.
- The host's small XP balance is the hard cap; a 402 `insufficient_balance` is shown plainly.
- `participants` locks the thread to host, planner and critic. Agents ignore threads not opened by the host or outside the demo node, so outsiders on a public node cannot make them spend tokens.
- The reply cap is counted from Matrix at handling time, never from memory. Reply idempotency key is the webhook delivery ID, and an agent skips if the latest message is already its own. A failure after Claude has answered (an empty answer, or Matrix rejecting the reply) is logged and not retried. Only a failure while reading the thread, which happens before Claude is called, is retried, up to 3 attempts. Each agent handles one notice at a time.
- Small token budget per turn, Sonnet rather than Opus.

## Tests

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest
```

Tests use stubbed SDKs; no live calls.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | The viewer: run tab, Cat Matrix tab, SDK-call side panel, webhook status |
| `requirements.txt` | Viewer deps (Gradio 6, python-dotenv, vendored `matrix_kred` wheel) |
| `requirements-dev.txt` | Adds the agent deps and pytest for running tests |
| `vendor/` | The `matrix_kred` wheel for the viewer |
| `space/requirements.txt` | Viewer deps for the Hugging Face Space (wheel by URL) |
| `agents/planner/` | Vercel project: planner agent |
| `agents/critic/` | Vercel project: critic agent (same shape as the planner) |
| `agents/*/app.py` | FastAPI app: verifies the webhook, enqueues the event |
| `agents/*/worker.py` | Queue subscriber: handles one notice |
| `agents/*/core.py` | Response rules, transcript, Claude call, reply |
| `agents/*/role.py` | The agent's name, who it answers to, and its prompt |
| `agents/*/pyproject.toml`, `uv.lock`, `vendor/` | Dependencies and the vendored wheel for that project |
| `tests/` | Response rules, webhook handling, viewer logic |
| `docs/superpowers/` | Design spec and plan |
