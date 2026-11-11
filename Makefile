# Shortcuts for the commands in README.md. Each recipe is the raw command, so
# you can copy it and run it without make. `make -n <target>` prints a recipe
# without running it.
#
# Terminal 1: make server   Terminal 2: make worker   Terminal 3: make run
# Terminal 4 (other people and systems): make upload / make review

# Must match TASK_QUEUE in app/workflows.py.
TASK_QUEUE := investment-demo

# make run NAME="Customer B". Any name containing "Smith" is flagged for review.
NAME ?= Customer A
# The customer ID the chat makes from NAME: lower case, spaces to hyphens.
ID ?= $(shell echo "$(NAME)" | tr '[:upper:] ' '[:lower:]-')
FILE ?= utility-bill.pdf
VERDICT ?= approve

.DEFAULT_GOAL := help
.PHONY: help setup server worker run upload review failure recovery reset test lint

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

# test -f keeps an .env you already have.
setup: ## Install dependencies and create .env from .env.example
	uv sync
	test -f .env || cp .env.example .env

# --db-filename keeps Workflows across a server restart.
server: ## Start a local Temporal Service (Temporal Web UI at http://localhost:8233)
	temporal server start-dev --db-filename demo.db

# The full path lets `make failure` find this demo's Worker and no other.
# PYTHONPATH lets app/worker.py import the app package from the repo root.
# The model comes from USE_FAKE_MODEL in .env (the fake model unless it is 0).
# REAL=1 or FAKE=1 overrides .env for this run.
MODEL_ENV = $(if $(REAL),USE_FAKE_MODEL=0,$(if $(FAKE),USE_FAKE_MODEL=1))

worker: ## Start the Worker (fake model by default; REAL=1 for OpenAI, spends tokens)
	$(MODEL_ENV) PYTHONPATH="$(CURDIR)" uv run python "$(CURDIR)/app/worker.py"

run: ## Chat with the agent as NAME (default "Customer A")
	uv run python -m app.chat "$(NAME)"

upload: ## Send a late proof-of-address document (ID=customer-a FILE=utility-bill.pdf)
	uv run python -m app.upload $(ID) $(FILE)

review: ## Compliance reviewer decides on a flagged customer (ID=alex-smith VERDICT=approve|decline)
	uv run python -m app.review $(ID) $(VERDICT)

# kill -9 is a crash, not a clean shutdown. The brackets stop pkill from
# matching the shell that runs this line. Best moment: while KYC waits for the
# proof-of-address document.
failure: ## Crash the Worker (kill -9) while a session is in flight
	@pkill -9 -f "$(CURDIR)/app/[w]orker.py" \
		&& echo "Worker killed. The Workflows wait in Temporal until a Worker returns." \
		|| echo "No Worker started with make worker or make recovery is running."

# The same command as `make worker`. The new Worker replays the Event History
# and carries on from the step that had not finished. Finished steps don't run
# again; a step in flight at the crash can.
recovery: ## Start a new Worker after make failure
	$(MODEL_ENV) PYTHONPATH="$(CURDIR)" uv run python "$(CURDIR)/app/worker.py"

# Terminates only this demo's running Workflows (sessions and their KYC child
# Workflows). It stops no server and deletes nothing.
reset: ## Terminate this demo's running Workflows
	temporal workflow terminate --yes --reason "make reset" \
		--query 'TaskQueue="$(TASK_QUEUE)" AND ExecutionStatus="Running"'

test: ## Run the tests (offline: fake model, no key, no tokens)
	uv run pytest

# Bugs only (see [tool.ruff.lint] in pyproject.toml); no formatter, on purpose.
lint: ## Check the code for bugs
	uv run ruff check .
