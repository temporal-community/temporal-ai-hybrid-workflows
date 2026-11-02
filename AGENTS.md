# AGENTS.md

How coding agents run, test, and change this repo. The README says what the demo shows; this file holds the rules.

This is a teaching demo for the Temporal & AI Series video on dynamic workflows. An agent explores investment options with a customer (the path is dynamic), a fixed KYC child Workflow opens the account (the guarantees are not), then the agent helps them get started. People read this code to learn, so clarity beats cleverness. Keep the story the video tells as carefully as the code.

## Commands

| Task | Command | Success looks like |
|---|---|---|
| Install | `make setup` (`uv sync`, then `test -f .env \|\| cp .env.example .env`) | Exit code 0 |
| Test | `make test` (`uv run pytest`) | `12 passed`, exit code 0. Offline: fake model, no key, no tokens |
| Lint | `make lint` (`uv run ruff check .`) | `All checks passed!` |
| Server | `make server` (`temporal server start-dev --db-filename demo.db`) | Temporal Web UI at http://localhost:8233 |
| Worker | `make worker` (fake model by default), or `make worker REAL=1` for OpenAI | `Worker listening on 'investment-demo'` |
| Run | `make run NAME="Customer A"` (`uv run python -m app.chat "Customer A"`) | The chat ends with `Model: N calls, N tokens.` (or `Model: fake. No model calls.`) |
| Late document | `make upload ID=customer-a FILE=utility-bill.pdf` | `Sent utility-bill.pdf to kyc-customer-a` |
| Compliance review | `make review ID=alex-smith VERDICT=approve` | `Compliance decision for kyc-alex-smith: approve` |
| Failure, then recovery | At the proof-of-address prompt press Enter, then `make failure`, `make recovery`, `make upload ID=...` | `Worker killed. ...`; after the upload, KYC carries on and `verify_identity` appears once in the Event History |
| Reset | `make reset` | `Started batch for job ID: ...` |

The chat waits for typed input. For an unattended run of the whole session, use the tests: `test_session_explores_then_runs_kyc_then_gets_started` plays a customer end to end with the fake model.

`make test` starts its own throwaway dev server on a free port, so it never touches a server on 7233.

## Always

- Run `make test` and `make lint` before you call a change done, and report their output.
- Keep Workflow code (`app/workflows.py`) deterministic: no model calls, network, file I/O, `os.environ`, `datetime.now()`, `random`, or `uuid.uuid4()`. Put them in an Activity, or use `workflow.now()`, `workflow.random()`, `workflow.uuid4()`. A new Worker replays Workflow code and must take the same path.
- Import Activity and agent modules into `app/workflows.py` inside `workflow.unsafe.imports_passed_through()`.
- Set `start_to_close_timeout` and a plain-words `summary=` on every `execute_activity` call. The summaries are what the video shows in the Temporal Web UI.
- Keep `retry_policy=MODEL_RETRY` (capped attempts) on `decide_next_step`, and `max_retries=0` on the OpenAI client, so Temporal is the only retry layer and every billed attempt shows in the Event History.
- Raise errors a retry cannot fix (missing key, rejected request) as `ApplicationError(..., non_retryable=True)`. Let everything else propagate.
- Check every model tool call with `check_tool_call` in `app/agent.py` before running it. Model output is untrusted input.
- Keep the agent read-only before KYC. Only `KycWorkflow` opens an account.
- Keep the UK wording: general guidance, not a personal recommendation or financial advice. Note that rules differ in other jurisdictions.
- Keep the guarantee claim precise in code comments and docs: Temporal guarantees the chosen path is recorded, retried, and resumable, not that the agent chose well.
- When you change a Makefile recipe, change the matching command in the README in the same edit.

## Ask first

- Anything that runs the Worker with `USE_FAKE_MODEL=0` or `make worker REAL=1`: it spends OpenAI tokens and dollars.
- Changing what the video shows: Workflow, Activity, Update, Signal or Query names; the Activity summaries; the KYC steps, their order, or the terms text; the Task Queue; the `session-<id>` and `kyc-<id>` Workflow IDs; the failure step.
- Reformatting code (`ruff format`, import sorting). The layout is tuned to be read on camera.
- Adding a file to `docs/`.
- Changing the prompts or the disclaimer wording in `app/agent.py`.
- Changing a sample customer in `docs/run-and-present.md`: presenters follow those answers.

## Never

- Print, log, or commit `.env` or any key.
- Let a test read a real key or call a paid API.
- Give the agent a write tool that bypasses KYC, or let the model decide whether KYC runs.
- Point `make reset` at anything this demo did not create, or stop a Temporal server someone else started.
- Name real funds, rates or providers. The products are made up.
- Make the real model the default. The fake model is what lets anyone run the demo with no key and no model calls.

## Intentional shortcuts

- Every tool and KYC step is a stub (`app/activities.py`); a real system would call identity, address and sanctions providers.
- The fake model (`app/fake_model.py`) is keyword rules. It exists so the demo and the tests run with no key.
- The real OpenAI path has no test, because tests must not call the API.
- `DEMO_SECONDS_PER_MONTH` shortens the review timer to seconds so it fires on camera.
- Any name containing "Smith" gets a possible sanctions match, to show the compliance review branch.

## Where things live

| Path | Role |
|---|---|
| `app/workflows.py` | `InvestmentSessionWorkflow` (agent loop, handoff, review timer) and `KycWorkflow` (fixed steps); timeouts and retry policy |
| `app/agent.py` | Prompts, tool schemas, phases, `check_tool_call` guardrails, the OpenAI call |
| `app/activities.py` | The model Activity, every tool, and the KYC steps |
| `app/fake_model.py` | Rule-based stand-in for the model |
| `app/products.py` | The made-up product catalogue |
| `app/worker.py` | The Worker and its `ACTIVITIES` list |
| `app/chat.py`, `app/upload.py`, `app/review.py` | The customer, a late document, a compliance reviewer |
| `tests/` | Offline tests |
| `docs/run-and-present.md` | Sample customers, what to type, and what to point at in the Web UI |
| `docs/why-kyc-always-runs.md` | Why code, not the prompt, guarantees KYC |

## Adding an agent tool

1. Write the Activity in `app/activities.py` and add it to `ACTIVITIES` in `app/worker.py`.
2. Add its schema to `TOOL_SCHEMAS` and its name to the right phase in `PHASES` (`app/agent.py`).
3. Add it to `TOOLS` and a label to `describe()` in `app/workflows.py`.
4. Add any exact rule to `check_tool_call`, and a test in `tests/test_demo.py`.
