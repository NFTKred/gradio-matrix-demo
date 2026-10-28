# Matrix Agents Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Gradio viewer plus two separately deployable Vercel agents (planner, critic) that hold a webhook-driven Claude conversation on a Matrix node, and a read-only view of the Cat Matrix node.

**Architecture:** The viewer (host identity) opens one thread per run with `participants=[planner, critic]` and polls it. Each agent is its own Vercel Python project: a FastAPI webhook route verifies the Matrix signature and enqueues the notice on Vercel Queues; a queue subscriber reads the thread back from Matrix, decides whether it is its turn, asks Claude, and replies with the webhook delivery ID as idempotency key.

**Tech Stack:** Python 3.12+, `matrix_kred` 0.1.0 (vendored wheel), Gradio 6, FastAPI, `vercel` (Queues Python SDK, beta), `anthropic`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-matrix-agents-demo-design.md`

## Global Constraints

- Agents use model `claude-sonnet-5-5`, one constant `MODEL` per agent. Call `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default", output_config={"effort": "low"}, max_tokens=4000)`; no `thinking` param. Check `stop_reason == "refusal"` before reading text.
- `REPLY_CAP` default `4` (env var, same value in viewer and both agents).
- Exactly one charged Matrix call per Start: `threads.create`. Everything else is `threads.reply` (free) or reads.
- The Cat Matrix node is read-only: viewer code calls only `threads.list`, `threads.get`, `threads.conversation` against it.
- Each `agents/<role>/` folder deploys to Vercel on its own (Vercel Root Directory = that folder); nothing in it imports from outside it.
- `matrix_kred` is installed from `vendor/matrix_kred-0.1.0-py3-none-any.whl` (copied from `C:\Users\Andrew Horn\code\matrixsdk\dist\`); swapping to the GitHub URL later must be a one-line change per project.
- No live API calls in tests. All Matrix, Claude and queue access is faked.
- Real environment variables take precedence over `.env` (`load_dotenv()` default), as in the earlier demo.
- Pin exact versions: `gradio==6.24.0` (as earlier demo); for `anthropic`, `vercel`, `fastapi`, `python-dotenv`, `pytest`, `httpx` pin the latest release at build time (`pip index versions <pkg>`), and confirm the pinned `anthropic` accepts `fallbacks=` on `beta.messages.create`.

## Review Focus

- **Agent ID prefix mismatch:** Matrix may report an agent as `usr_…` where config holds `agt_…` (spec: "an agent is a user"; both prefixes accepted). Expected: IDs compare equal on the part after the prefix. Pinned in Task 1 (`test_same_id_ignores_prefix`).
- **Duplicate or out-of-order notices:** a redelivered notice after the agent already replied, or the critic being notified of the opening post. Expected: no Claude call, no post. Pinned in Task 1 (`test_skip_when_latest_is_own`, `test_critic_ignores_opening_post`).
- **Outsider or foreign thread:** a thread on another node or opened by someone other than host. Expected: ignored before any Claude call. Pinned in Task 1 and Task 2 (`test_take_turn_skips_without_calling_claude`).
- **Start fails (402 insufficient balance, network error):** Expected: plain message in the status line, button re-enabled, no traceback. Pinned in Task 5 (`test_start_run_reports_insufficient_balance`).
- **Agents never answer (webhook misconfigured):** Expected: viewer stops polling after the timeout and says to check webhook status. Pinned in Task 5 (`test_start_run_times_out`).

---

## File Structure

```
.gitignore
requirements.txt                 viewer runtime deps (incl. ./vendor wheel)
requirements-dev.txt             -r requirements.txt + agent deps + pytest (for running all tests locally)
vendor/matrix_kred-0.1.0-py3-none-any.whl
main.py                          Gradio viewer: config, SDK-call log, run/poll, cat view, UI
README.md
tests/conftest.py                FakeMatrix, signing helper, module loader for agent folders
tests/test_agent_core.py
tests/test_agent_webhook.py
tests/test_agent_copies.py
tests/test_viewer.py
agents/planner/
  pyproject.toml                 deps, [tool.uv.sources] wheel, [[tool.vercel.subscribers]]
  vendor/matrix_kred-0.1.0-py3-none-any.whl
  role.py                        ROLE_NAME, SYSTEM_PROMPT, ANSWERS_TO_ENV   (differs per agent)
  core.py                        config, turn rules, transcript, Claude call, take_turn (identical in both)
  app.py                         FastAPI webhook route -> queue           (identical in both)
  worker.py                      queue subscriber -> core.take_turn       (identical in both)
agents/critic/                   same files; only role.py differs
```

Agent modules are flat (`core`, `role`, `app`, `worker`) because each folder is a deploy root. Tests load them by file path with a fresh module namespace per folder (helper in `tests/conftest.py`).

---

### Task 1: Agent turn rules (planner) + project scaffolding

**Files:**
- Create: `.gitignore` (`.env`, `.venv/`, `.idea/`, `__pycache__/`, `.vercel/`, `.pytest_cache/`)
- Delete: `main.py` (PyCharm sample; replaced in Task 5)
- Create: `vendor/…whl`, `agents/planner/vendor/…whl` (copies of the SDK wheel)
- Create: `requirements.txt`, `requirements-dev.txt`
- Create: `agents/planner/role.py`, `agents/planner/core.py`
- Create: `tests/conftest.py`, `tests/test_agent_core.py`

**Interfaces:**
- Produces (`core.py`):
  - `@dataclass(frozen=True) class AgentConfig: self_id: str; host_id: str; answers_to: frozenset[str]; demo_node_id: str; reply_cap: int`
  - `load_config(env: Mapping[str, str] = os.environ) -> AgentConfig`: `self_id=MATRIX_SELF_ID`, `host_id=MATRIX_HOST_ID`, `answers_to` = values of the env names in `role.ANSWERS_TO_ENV`, `demo_node_id=MATRIX_DEMO_NODE_ID`, `reply_cap=int(REPLY_CAP or 4)`.
  - `same_id(a: str | None, b: str | None) -> bool`: compares the part after the first `_`; `None` never matches.
  - `skip_reason(thread: models.ThreadResource, replies: list[models.ReplyResource], cfg: AgentConfig) -> str | None`: `None` means respond. Checks, in order: thread node is not `cfg.demo_node_id` → `"other node"`; thread author (`thread.user`) is not host → `"not a host thread"`; `len(replies) >= cfg.reply_cap` → `"reply cap reached"`; latest author (last reply's `user`, else `thread.user`) is self → `"already answered"`; latest author not in `answers_to` → `"not my turn"`.
  - `transcript(thread, replies, names: Mapping[str, str]) -> str`: one block per message, oldest first, `"<Name>: <text>"`, opening post labelled `"Task"`; names looked up with `same_id`, unknown authors `"Someone"`.
- Produces (`role.py`, planner): `ROLE_NAME = "Planner"`, `ANSWERS_TO_ENV = ("MATRIX_HOST_ID", "MATRIX_CRITIC_ID")`, `SYSTEM_PROMPT` (propose, then revise, a concrete plan for the task in the transcript; take the critic's points seriously; at most ~150 words; plain text).
- Produces (`tests/conftest.py`): `load_agent(role: str) -> SimpleNamespace(core, role, app, worker)` loading `agents/<role>/*.py` via `importlib.util` under unique module names with the folder on `sys.path` while loading (missing modules allowed until their task exists); `make_thread(**kw)`, `make_reply(**kw)` building SDK models; `FakeMatrix` with `threads.get/conversation/reply/create/list`, `ledger.balance`, `agent_webhooks.get`, recording `calls: list[tuple[str, dict]]`, and settable return values / exceptions.

- [ ] **Step 1: Scaffold.** Copy the wheel into both `vendor/` paths. `requirements.txt`: `gradio==6.24.0`, `python-dotenv==…`, `./vendor/matrix_kred-0.1.0-py3-none-any.whl`. `requirements-dev.txt`: `-r requirements.txt`, `fastapi==…`, `vercel==…`, `anthropic==…`, `httpx==…`, `pytest==…`. Create `.venv` and install `requirements-dev.txt`.

- [ ] **Step 2: Write failing tests** in `tests/test_agent_core.py` (planner loaded via `load_agent("planner")`; config `self=agt_p`, `host=agt_h`, `answers_to={agt_h, agt_c}`, node `node_d`, cap 4):
  - `test_same_id_ignores_prefix`: `same_id("agt_abc", "usr_abc")` is True; `same_id("agt_abc", "agt_xyz")` False; `same_id(None, "agt_abc")` False.
  - `test_responds_to_host_opening_post`: thread by `usr_h` on `node_d`, no replies → `None`.
  - `test_ignores_other_node` → `"other node"`; `test_ignores_non_host_thread` (thread by `agt_x`) → `"not a host thread"`.
  - `test_stops_at_reply_cap`: 4 replies → `"reply cap reached"`.
  - `test_skip_when_latest_is_own`: last reply by `usr_p` → `"already answered"`.
  - `test_responds_to_critic_reply`: last reply by `agt_c` → `None`.
  - `test_critic_ignores_opening_post`: critic config (`self=agt_c`, `answers_to={agt_p}`), no replies → `"not my turn"`.
  - `test_transcript_orders_and_labels`: thread text `"Plan a picnic"`, replies by planner then critic → `"Task: Plan a picnic\n\nPlanner: …\n\nCritic: …"`.
  - `test_load_config_reads_env`: given a dict env, fields match; missing `REPLY_CAP` → 4.

- [ ] **Step 3: Run to verify failure.** `.venv/Scripts/python.exe -m pytest tests/test_agent_core.py -v` → FAIL (module/attribute missing).

- [ ] **Step 4: Implement** `role.py` and the `core.py` functions above.

- [ ] **Step 5: Run to verify pass.** Same command → all PASS.

- [ ] **Step 6: Commit** `feat: agent turn rules and project scaffolding`.

---

### Task 2: Agent turn execution (planner)

**Files:**
- Modify: `agents/planner/core.py`
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Consumes: Task 1 `AgentConfig`, `skip_reason`, `transcript`.
- Produces (`core.py`):
  - `MODEL = "claude-sonnet-5-5"`
  - `ask_claude(client: anthropic.Anthropic, system: str, text: str) -> str | None`: one `beta.messages.create` call per Global Constraints, user message = `text`; returns joined text blocks, or `None` when `stop_reason == "refusal"` (log category via `print`).
  - `take_turn(matrix: MatrixClient, ask: Callable[[str, str], str | None], cfg: AgentConfig, thread_id: str, delivery_id: str, names: Mapping[str, str]) -> str`: `matrix.threads.get` + `list(matrix.threads.conversation(...))`; if `skip_reason` → return `"skipped: <reason>"` with no `ask` call; else `ask(role.SYSTEM_PROMPT, transcript(...))`; `None` → `"refused"`; else `matrix.threads.reply(thread_id, text=..., idempotency_key=delivery_id)` → `"replied <reply.id>"`. Matrix `PermissionDeniedError`/`NotFoundError` on reply → return `"failed: <code>"` (no retry).
  - `agent_names(cfg_env: Mapping[str, str]) -> dict[str, str]`: maps `MATRIX_HOST_ID`→"Host", `MATRIX_PLANNER_ID`→"Planner", `MATRIX_CRITIC_ID`→"Critic" for whichever are set.

- [ ] **Step 1: Write failing tests:**
  - `test_take_turn_replies_with_delivery_key`: FakeMatrix thread by host, no replies; `ask` stub returns `"A plan"` → result starts `"replied"`; FakeMatrix recorded `reply` with `text="A plan"`, `idempotency_key="dlv_1"`; stub received `role.SYSTEM_PROMPT` and a transcript containing the task text.
  - `test_take_turn_skips_without_calling_claude`: thread opened by `agt_x` → result `"skipped: not a host thread"`; `ask` never called; no `reply` recorded.
  - `test_take_turn_refusal_posts_nothing`: `ask` returns `None` → `"refused"`, no `reply` recorded.
  - `test_take_turn_reply_forbidden`: FakeMatrix `reply` raises `matrix_kred.PermissionDeniedError` → result starts `"failed"`.
  - `test_ask_claude_refusal_returns_none`: fake anthropic client whose `beta.messages.create` returns an object with `stop_reason="refusal"`, `content=[]`, `stop_details=None` → `None`; and asserts the call kwargs include `model="claude-sonnet-5-5"`, `fallbacks="default"`, `betas=["server-side-fallback-2026-07-01"]`, `max_tokens=4000`.

- [ ] **Step 2: Run to verify failure.** `… -m pytest tests/test_agent_core.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** `MODEL`, `ask_claude`, `take_turn`, `agent_names`.
- [ ] **Step 4: Run to verify pass.** → all PASS.
- [ ] **Step 5: Commit** `feat: agent takes a turn via Matrix and Claude`.

---

### Task 3: Planner webhook route, queue worker, Vercel config

**Files:**
- Create: `agents/planner/app.py`, `agents/planner/worker.py`, `agents/planner/pyproject.toml`
- Test: `tests/test_agent_webhook.py`; add `sign(body: bytes, secret: str, t: int | None = None) -> dict[str, str]` to `tests/conftest.py` (returns `X-Matrix-Signature: t=<t>,v1=<hex hmac_sha256(secret, f"{t}.".encode()+body)>`, `X-Matrix-Event`, `X-Matrix-Delivery`).

**Interfaces:**
- Consumes: Task 2 `take_turn`, `ask_claude`, `load_config`, `agent_names`.
- Produces:
  - `app.py`: `app = FastAPI()`; `NOTICES = Topic[dict]("matrix-notices")` defined in `worker.py` and imported here; `POST /api/webhook`: read raw body → `webhooks.construct_event(body, request.headers, os.environ["MATRIX_WEBHOOK_SECRET"])`; `WebhookVerificationError` → 400; `WebhookPingEvent` → 200 `{"ok": true}`; `MessageCreatedEvent` whose `data.node.id` ≠ `MATRIX_DEMO_NODE_ID` → 200 `{"ignored": "other node"}` (no enqueue); else `await send(NOTICES, event.model_dump(mode="json"), idempotency_key=event.delivery)` → 200 `{"queued": true}`; `DuplicateIdempotencyKeyError` → 200 `{"queued": true}`. `GET /` → `{"agent": role.ROLE_NAME}` (deploy check).
  - `worker.py`: `process_payload(payload: dict) -> str` (parse `MessageCreatedEvent`, build `MatrixClient()` and `anthropic.Anthropic()`, call `take_turn(..., thread_id=event.data.thread.id, delivery_id=event.delivery, ...)`, print and return the outcome); `@subscribe(topic=NOTICES, max_attempts=3, retry_after=60) async def handle_notice(message: Message[dict]) -> None` → `await asyncio.to_thread(process_payload, message.payload)`.
  - `pyproject.toml`: `requires-python = ">=3.12"`; dependencies `fastapi`, `vercel`, `anthropic`, `matrix-kred` (pinned per Global Constraints); `[tool.uv.sources] matrix-kred = { path = "vendor/matrix_kred-0.1.0-py3-none-any.whl" }`; `[[tool.vercel.subscribers]] entrypoint = "worker"`.

- [ ] **Step 1: Write failing tests** (FastAPI `TestClient`; monkeypatch env `MATRIX_WEBHOOK_SECRET=whsec_test`, `MATRIX_DEMO_NODE_ID=node_d`; monkeypatch `app.send` with a recorder):
  - `test_bad_signature_rejected`: wrong secret → 400, nothing sent.
  - `test_ping_acknowledged`: signed ping body → 200, nothing sent.
  - `test_message_enqueued_with_delivery_key`: signed `message.created` for `node_d` → 200; recorder got topic `NOTICES`, payload with `delivery == "dlv_1"`, `idempotency_key="dlv_1"`.
  - `test_other_node_not_enqueued`: node `node_z` → 200, nothing sent.
  - `test_duplicate_enqueue_is_ok`: recorder raises `DuplicateIdempotencyKeyError` → 200.
  - `test_process_payload_runs_turn`: monkeypatch `worker.MatrixClient` → FakeMatrix, `worker.anthropic.Anthropic` → stub, `worker.ask_claude` → returns `"ok"`; payload from a valid event → returns string starting `"replied"`.
- [ ] **Step 2: Run to verify failure.** `… -m pytest tests/test_agent_webhook.py -v` → FAIL.
- [ ] **Step 3: Implement** `app.py`, `worker.py`, `pyproject.toml`.
- [ ] **Step 4: Run to verify pass.** → PASS. Also `cd agents/planner && uv lock` (or `uv pip compile pyproject.toml`) resolves without error.
- [ ] **Step 5: Commit** `feat: planner webhook route and queue worker`.

---

### Task 4: Critic agent

**Files:**
- Create: `agents/critic/` with `core.py`, `app.py`, `worker.py`, `pyproject.toml`, `vendor/…whl` copied from planner (pyproject `name` changed), and its own `role.py`
- Test: `tests/test_agent_copies.py`

**Interfaces:**
- Produces (`agents/critic/role.py`): `ROLE_NAME = "Critic"`, `ANSWERS_TO_ENV = ("MATRIX_PLANNER_ID",)`, `SYSTEM_PROMPT` (find the weakest points in the planner's latest plan and suggest specific fixes; don't write a new plan; at most ~120 words; plain text).

- [ ] **Step 1: Write failing tests:**
  - `test_shared_files_identical`: `core.py`, `app.py`, `worker.py` are byte-identical between `agents/planner` and `agents/critic`.
  - `test_critic_role_answers_planner_only`: `load_agent("critic").core.load_config(env)` gives `answers_to == {planner_id}`.
  - `test_critic_turn_after_planner`: FakeMatrix thread by host, one reply by planner; critic `take_turn` with stub `ask` → `"replied …"`.
- [ ] **Step 2: Run to verify failure.** `… -m pytest tests/test_agent_copies.py -v` → FAIL.
- [ ] **Step 3: Create the critic folder.**
- [ ] **Step 4: Run all tests.** `… -m pytest -v` → all PASS.
- [ ] **Step 5: Commit** `feat: critic agent`.

---

### Task 5: Viewer: start and watch a run

**Files:**
- Create: `main.py`
- Test: `tests/test_viewer.py`

**Interfaces:**
- Produces (`main.py`):
  - `@dataclass(frozen=True) class ViewerConfig` with `host_token, host_id, planner_id, critic_id, demo_node_id, cat_node_id, planner_token: str | None, critic_token: str | None, reply_cap: int`; `load_config(env=os.environ) -> ViewerConfig` (env names as in the spec's Configuration section).
  - `POLL_SECONDS = 3`, `RUN_TIMEOUT_SECONDS = 300`.
  - `log_call(log: list[dict], code: str, result: Any) -> None`: appends `{"call": code, "result": <model_dump(mode="json") or list thereof, or str for errors>}`.
  - `to_chat(thread, replies, names) -> list[dict]`: Gradio messages; opening post → `{"role": "user", "content": thread text}`; each reply → `{"role": "assistant", "content": "**<Name>:** <text>"}`.
  - `host_balance(host: MatrixClient, log) -> str` via `host.ledger.balance()`; shows `amount_decimal` + `currency`.
  - `start_run(task: str, cfg: ViewerConfig, host: MatrixClient, *, sleep=time.sleep, clock=time.monotonic) -> Iterator[tuple[list[dict], str, str]]` yielding `(chat, log_json, status)`: `threads.create(cfg.demo_node_id, title=task[:80], text=task, participants=[cfg.planner_id, cfg.critic_id], idempotency_key=uuid4().hex)` logged as code text; then loop: `threads.get` + `conversation` (logged only when the reply count changes, to keep the panel readable), yield, stop when replies ≥ `cfg.reply_cap` (status `"Done: N replies."`) or `clock()` exceeds timeout (status mentions checking the agents' webhook status). Empty task → single yield with status `"Enter a task first."` and no API call. `PaymentRequiredError` → status `"Host can't pay for a thread: <e.code> …"`; other `MatrixError` → status with `e.message`/str; both with no create retried.
  - `names_for(cfg) -> dict[str, str]` (`Host`, `Planner`, `Critic`).

- [ ] **Step 1: Write failing tests** (FakeMatrix; fake `sleep` no-op; fake `clock` from an iterator):
  - `test_start_run_creates_one_thread_with_participants`: exhausting the generator records exactly one `create` with `participants=[planner, critic]`, an `idempotency_key`, `text=task`.
  - `test_start_run_stops_at_cap`: FakeMatrix conversation returns 0 then 4 replies → last status `"Done: 4 replies."`; last chat has 5 messages.
  - `test_start_run_times_out`: replies stay 0; clock passes 300 → last status contains `"webhook"`.
  - `test_start_run_reports_insufficient_balance`: `create` raises `PaymentRequiredError` (code `insufficient_balance`) → one yield, status contains `"insufficient_balance"`, no further calls.
  - `test_start_run_requires_task`: `""` → status `"Enter a task first."`, no calls.
  - `test_to_chat_labels_authors`: replies by `usr_p` and `agt_c` render as `**Planner:**` / `**Critic:**` (prefix-insensitive).
- [ ] **Step 2: Run to verify failure.** `… -m pytest tests/test_viewer.py -v` → FAIL.
- [ ] **Step 3: Implement** the functions above (no UI yet; `main.py` must import without network access or env vars set).
- [ ] **Step 4: Run to verify pass.** → PASS.
- [ ] **Step 5: Commit** `feat: viewer starts and watches a run`.

---

### Task 6: Viewer: Cat Matrix tab, webhook status, Gradio UI

**Files:**
- Modify: `main.py`
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: Task 5 functions.
- Produces (`main.py`):
  - `cat_threads(client: MatrixClient, cfg, log) -> list[tuple[str, str]]`: `threads.list(cfg.cat_node_id, limit=20)` → `(label, thread_id)` choices, label = title or first 60 chars of text.
  - `cat_conversation(client, thread_id: str, log) -> list[dict]`: `threads.get` + `conversation`, rendered with `to_chat` using author `name` from `reply.author.name` when no config name matches.
  - `webhook_status(cfg) -> str`: for each of planner/critic with a token configured, `MatrixClient(token).agent_webhooks.get(agent_id)` → one line `"<Name>: enabled=…, last_status=…, failures=…, last_error=…"`; no tokens → `"Set MATRIX_PLANNER_TOKEN / MATRIX_CRITIC_TOKEN to see webhook delivery status."`; errors → the error message on that line.
  - `build_ui(cfg) -> gr.Blocks`: Tab "Agents talking": markdown intro (Matrix is the wire; agents run on Vercel and their SDK calls are in Vercel logs; Start spends one thread charge from host), host balance, task textbox + `gr.Examples` (3 tasks), Start button, Chatbot, status markdown, side `gr.Code(language="json")` call log, webhook-status markdown + Refresh button. Start click: first disable button, `.then(start_run …)` streaming, `.then(re-enable)`. Tab "Cat Matrix (read-only)": Refresh threads, dropdown, Chatbot, call log.
  - `if __name__ == "__main__": load_dotenv(); cfg = load_config(); build_ui(cfg).launch()`.

- [ ] **Step 1: Write failing tests:**
  - `test_cat_view_is_read_only`: running `cat_threads` then `cat_conversation` against FakeMatrix records only `list`, `get`, `conversation` calls.
  - `test_cat_threads_labels`: thread with no title uses text prefix.
  - `test_webhook_status_without_tokens`: returns the "Set MATRIX_PLANNER_TOKEN…" text.
  - `test_build_ui_constructs`: `build_ui(cfg)` returns a `gr.Blocks` without network calls (host client creation is lazy or faked).
- [ ] **Step 2: Run to verify failure.** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** `… -m pytest -v` → all PASS. Then `.venv/Scripts/python.exe main.py` with a dummy `.env` and confirm the page loads at http://127.0.0.1:7860 with both tabs (API calls may error; the UI must not crash).
- [ ] **Step 5: Commit** `feat: viewer cat tab, webhook status and UI`.

---

### Task 7: README and deployment notes

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README** following the earlier demo's README shape: what it is (scratchpad; NFT.NYC vibesprint line only if the user confirms it applies; ask, don't assume), the picture (viewer ↔ Matrix ↔ two Vercel agents), example tasks, local setup (`python -m venv .venv`, install `requirements.txt`, `.env` keys from the spec), the spec's five Setup steps verbatim in substance, including **no allowance to host**, deploying each agent folder as its own Vercel project (Root Directory `agents/planner` / `agents/critic`, env vars per the spec, enable Queues), `POST /agents/{id}/webhook/test` check, swapping the vendored wheel for the GitHub URL later, and a "Heads up: this talks to the real API" section: thread creation costs XP, each agent turn costs Claude tokens, keys aren't scopable so Cat Matrix read-only is enforced by code only, guardrails list from the spec. Files table.
- [ ] **Step 2: Verify** every env var named in the README exists in `load_config` (viewer) or `core.load_config` / `app.py` / `worker.py` (agents), and vice versa: `grep -rhoE "MATRIX_[A-Z_]+|REPLY_CAP|ANTHROPIC_API_KEY" main.py agents/planner README.md | sort -u` lists match.
- [ ] **Step 3: Run all tests** → PASS.
- [ ] **Step 4: Commit** `docs: README with setup and live-API notes`.
