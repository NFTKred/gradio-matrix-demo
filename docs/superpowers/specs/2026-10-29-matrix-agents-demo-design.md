# Matrix agents demo: design

Date: 2026-10-29
Status: draft, awaiting approval

## Intent

A deliberately simple demo of the Matrix API (`https://api.matrix.kred`, docs at `/redoc`,
spec at `/openapi.json`) and its Python SDK `matrix_kred`, aimed at developers who use
Hugging Face AI tooling. They should leave understanding that **Matrix can be the
communication layer between agents**: agents hosted separately, built separately, talking
through Matrix rather than through each other.

It is a scratchpad, not a product, in the spirit of the earlier Domains.Kred demo
(`C:\Users\Andrew Horn\code\gradio-demo`): little app code in the way, the API visible, and
an honest README about what is live and what costs money.

## Goals

1. Show two Claude-powered agents (a **planner** and a **critic**) holding a conversation on a
   Matrix node, driven entirely by Matrix webhooks: Matrix notifies each agent when the other
   posts, and the agent replies through the SDK.
2. Make it obvious the agents are independent services: two separate webhook endpoints, each
   deployable on its own to Vercel.
3. Show that Matrix is shared ground: a read-only view of the Cat Matrix node
   (the conversations behind <https://catmatrix.kred>, cat-roleplaying agents built by a
   different demo), read with a few lines of SDK code.
4. Show the SDK code doing each step, next to the result.

## Non-goals

- Webhooks-free polling agents, payments between agents (gifts/allowances), a node/agent
  directory browser, listing past runs.
- Automated setup: node creation and agent registration are done by hand (see Setup).
- Hosting the Gradio app anywhere but locally.

## Anti-goals (failure even if it works)

- **Accidentally spending lots of XP or Claude tokens.** This is the user's stated top concern.
  Every guardrail below exists for it.
- Posting anything to the Cat Matrix node. Read-only, always.
- The app passing messages between agents in-process. Everything an agent knows about the
  conversation must come from Matrix.

## Architecture

Three parts in one repo:

```
main.py                 Gradio viewer, runs locally (python main.py)
agents/planner/         Vercel project: planner agent
agents/critic/          Vercel project: critic agent
```

The planner and critic folders are each a self-contained Vercel Python project with its own
dependencies and environment variables, so they can be deployed (and understood) separately.
They will share the same shape; whether they share code through a common module or are
deliberate near-duplicates is the builder's call, but each folder must deploy on its own.

### Identities

Three Matrix agents, registered by hand by the user:

| Agent | Used by | Needs |
|---|---|---|
| host | Gradio viewer | XP to pay one thread charge per run; no node roles |
| planner | planner Vercel project | webhook set to its Vercel URL; no node roles |
| critic | critic Vercel project | webhook set to its Vercel URL; no node roles |

Matrix keys are not scopable; each token can do anything its identity can. The node is
public and must not hold new posts for approval, so no roles are needed to post, reply, or read.

### Conversation flow

1. Developer enters a task in the viewer and presses Start.
2. Viewer (as host) calls `threads.create(node_id, title=..., text=<task>,
   participants=[planner_id, critic_id], idempotency_key=<fresh per Start>)`. This is the only
   charged call per run. `participants` means only host, planner and critic can reply.
3. Matrix sends the planner and critic each a `message.created` notice (they are participants).
4. Each agent's webhook route: verify signature (`matrix_kred.webhooks.construct_event`),
   enqueue the event on that agent's Vercel Queue, return 200 immediately (Matrix requires
   2xx within 5 s; Python on Vercel has no `waitUntil`, and FastAPI `BackgroundTasks` are not
   reliable there, so Vercel Queues is the chosen mechanism. Its Python SDK is in beta; the user
   accepted that).
5. Each agent's queue subscriber decides whether to respond (rules below). If so, it reads the
   whole thread back from Matrix (`threads.conversation`), asks Claude for its turn, and posts
   with `threads.reply(thread_id, text=..., idempotency_key=<webhook delivery id>)`, so a
   duplicate notice can never produce a second post.
6. Matrix notifies the other agent; repeat until the cap.
7. The viewer polls the thread and shows each message as it appears.

**Response rules (my call):**
- Ignore any thread not opened by the host agent ID, and any thread outside the demo node.
- Ignore own messages (Matrix does not send them anyway).
- Planner responds to the host's opening post and to critic replies.
- Critic responds only to planner replies (it ignores the opening post).
- Both stop when the thread already has `REPLY_CAP` replies (default 4: planner, critic,
  planner, critic). Counted from Matrix at handling time, never from memory.
- Skip if the latest message in the thread is already the agent's own (my call). The reply
  idempotency key stops a duplicate *post*; this check also stops a redelivered notice or
  queue retry from paying for a second Claude call.
- Ping events (`webhook/test`) are acknowledged and otherwise ignored.

### Claude

- Anthropic Python SDK. Model **`claude-sonnet-5-5`** (my call, for cost; one constant to change).
- Modest `max_tokens` per turn and system prompts asking for brief replies (a few short
  paragraphs). Exact values are the builder's call.
- Planner prompt: propose and revise a concrete plan for the task. Critic prompt: point out
  weaknesses and suggest specific improvements. Wording is the builder's call.
- Handle a refusal stop reason gracefully (post nothing, log it) as the earlier demo did.

## Gradio viewer (`main.py`)

Mirrors the earlier demo: one file, `gr.Blocks`, a main column and a side panel.

**Tab 1: Agents talking**
- Task textbox, example tasks, Start button (disabled while a run is in progress).
- Host XP balance (`client.ledger` balance) shown before starting.
- Conversation pane: the thread as read back from Matrix, author-labelled, updating live
  (Gradio generator polling the thread every few seconds) until `REPLY_CAP` replies appear or
  a timeout (a few minutes) passes, at which point it says it stopped waiting.
- Side panel: each SDK call the viewer made (Python snippet of the call, e.g.
  `host.threads.create(...)`, plus the JSON it returned), in the spirit of the earlier demo's
  tool-call panel. Also a short note that the agents' own SDK calls run on Vercel (see their logs).
- Webhook status for planner and critic (`GET /agents/{id}/webhook`: last attempt, last status,
  failures), shown if their tokens are configured locally (my call: optional; the viewer works
  without them).

**Tab 2: Cat Matrix (read-only)**
- Lists recent threads on the Cat Matrix node; selecting one shows its conversation.
- Same SDK-call side panel.
- The code never calls a write operation against this node. Because keys are not scopable, this
  is enforced only by the code; the README says so.

## Configuration

Viewer `.env` (real env vars take precedence, as before):

```
MATRIX_HOST_TOKEN=
MATRIX_PLANNER_ID=
MATRIX_CRITIC_ID=
MATRIX_DEMO_NODE_ID=
MATRIX_CAT_NODE_ID=
MATRIX_PLANNER_TOKEN=     # optional, for webhook status
MATRIX_CRITIC_TOKEN=      # optional, for webhook status
REPLY_CAP=4               # must match the agents; tells the viewer when to stop polling
```

The viewer does not call Claude, so it needs no Anthropic key.

Each Vercel agent project (Vercel environment variables): its own `MATRIX_API_TOKEN`,
`MATRIX_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `MATRIX_HOST_ID`, `MATRIX_DEMO_NODE_ID`,
the other agent's ID, and `REPLY_CAP`.

## Setup (documented in README, done by hand)

1. Create a public demo node (no approval hold) on a domain the user controls.
2. Register host, planner, critic with `POST /agents`; save each token (shown once).
3. Deploy `agents/planner` and `agents/critic` to Vercel; enable Vercel Queues.
4. Set each agent's webhook to its Vercel URL; save each signing secret (shown once).
   Use `POST /agents/{id}/webhook/test` to confirm delivery.
5. Fund the host with a small XP balance: the hard spending limit. Do **not** grant the host an
   allowance from the owning account; an allowance lets it draw past its own balance and
   removes that cap.

## Guardrails (summary)

- One charged call per Start; button disabled during a run; fresh idempotency key per Start.
- Small host XP balance is the hard cap; 402 `insufficient_balance` is shown plainly.
- `participants` locks the thread to the three identities; agents ignore threads not opened by
  host in the demo node, so outsiders on a public node cannot make agents spend tokens.
- Reply cap counted from Matrix; reply idempotency key = webhook delivery ID.
- Small `max_tokens`, Sonnet rather than Opus.

## Dependencies and constraints

- Python 3.10+ (SDK minimum). Gradio 6 and the Anthropic SDK, pinned as in the earlier demo.
- `matrix_kred` is **not on PyPI yet**. Local checkout at `C:\Users\Andrew Horn\code\matrixsdk`
  (a built wheel exists at `dist/matrix_kred-0.1.0-py3-none-any.whl`); it will be published
  via GitHub. The Vercel projects need it installable at build time, so the builder must pick
  a mechanism that works today (e.g. vendored wheel) and is easy to switch to the GitHub URL.
- Vercel Python runtime (FastAPI) and Vercel Queues Python SDK (`vercel` / `vercel-queue`, beta).

## Left to the builder

- Exact prompts, `max_tokens`, polling interval, timeout, example tasks.
- Shared-module vs duplicated agent code (as long as each folder deploys alone).
- How the wheel is shipped to Vercel.
- Tests: light, in the spirit of a demo (e.g. response-rule logic and webhook handling with a
  stubbed SDK). No live calls in tests.

## Calls made by Claude (not explicitly decided by the user)

- SDK directly, no MCP. Three identities (host opens and pays). Response rules and `REPLY_CAP=4`.
- `claude-sonnet-5-5` for agents. Repo layout. Optional webhook-status panel.
