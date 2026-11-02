"""Tests for the investment demo. Offline: fake model, no API key, no tokens.

The Workflow tests run on a throwaway local dev server (see conftest.py) with
the real Activities. A few register a stand-in under the name
decide_next_step, which is how you script a model's failures without calling it.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowUpdateFailedError,
    WorkflowUpdateStage,
)
from temporalio.converter import PayloadConverter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from app import activities
from app.agent import check_tool_call
from app.models import ComplianceDecision, Customer, KycInput, Usage
from app.worker import ACTIVITIES
from app.workflows import InvestmentSessionWorkflow, KycWorkflow

WORKFLOWS = [InvestmentSessionWorkflow, KycWorkflow]

# A non-deterministic call makes a Workflow Task retry forever. This turns that
# hang into a failed test.
TEST_TIMEOUT = timedelta(seconds=60)


def new_customer(name: str = "Test Customer") -> Customer:
    """A customer with a unique ID, so tests never share Workflow IDs."""
    return Customer(f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}", name)


async def scheduled_activities(handle: WorkflowHandle) -> list[tuple[str, str]]:
    """(Activity type, summary) for each Activity, in the order the Workflow ran them."""
    history = await handle.fetch_history()
    return [
        (event.activity_task_scheduled_event_attributes.activity_type.name,
         PayloadConverter.default.from_payload(event.user_metadata.summary, str))
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def wait_for_stage(kyc: WorkflowHandle, stage: str) -> None:
    """Poll the KYC progress Query, as the chat does, until it reaches a stage."""
    for _ in range(100):
        if (await kyc.query(KycWorkflow.progress)).stage == stage:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"KYC never reached {stage!r}")


async def complete_kyc(kyc: WorkflowHandle) -> None:
    """Send the document and agree to the terms: the customer's part of KYC."""
    await kyc.signal(KycWorkflow.document_uploaded, "utility-bill.pdf")
    await wait_for_stage(kyc, "consent")
    await kyc.execute_update(KycWorkflow.give_consent, "I agree")


# --- Guardrails: plain functions, no Temporal needed ---


def test_agent_cannot_use_a_tool_from_another_phase():
    error = check_tool_call("explore", "schedule_review", {"months": 12}, [])
    assert error == "Tool not available in this phase: schedule_review"


def test_agent_can_only_present_products_it_searched_for():
    history = [{"role": "tool", "tool": "search_products",
                "result": [{"id": "cash-isa", "name": "Easy Access Cash ISA", "isa": True}]}]
    made_up = {"options": [{"product_id": "global-index", "name": "Global"}], "explanation": ""}
    assert check_tool_call("explore", "present_options", made_up, history) == (
        "Only present products returned by search_products.")


def test_closing_message_cannot_ask_a_question():
    error = check_tool_call("get_started", "end_conversation", {"message": "All good?"}, [])
    assert error is not None


# --- The model Activity ---


async def test_fake_model_makes_no_model_calls():
    decision = await ActivityEnvironment().run(activities.decide_next_step, "explore", [], False)
    assert decision["tool"] == "ask_customer"
    assert decision["usage"] == {"model_calls": 0, "tokens": 0}


async def test_real_model_without_a_key_fails_fast(monkeypatch):
    monkeypatch.setenv("USE_FAKE_MODEL", "0")
    with pytest.raises(ApplicationError) as error:
        await ActivityEnvironment().run(activities.decide_next_step, "explore", [], False)
    assert error.value.non_retryable  # retrying cannot fix a missing key
    assert "OPENAI_API_KEY" in str(error.value)


# --- The bridge: fixed KYC, same steps and order for every customer ---


async def run_kyc(env: WorkflowEnvironment, customer: Customer, task_queue: str) -> WorkflowHandle:
    """Start a KycWorkflow on its own and play the customer's part."""
    kyc = await env.client.start_workflow(
        KycWorkflow.run, KycInput(customer, "Global Index Fund"),
        id=f"kyc-{customer.customer_id}", task_queue=task_queue,
        execution_timeout=TEST_TIMEOUT,
    )
    await complete_kyc(kyc)
    return kyc


async def test_kyc_runs_the_same_steps_in_the_same_order_for_everyone(env):
    task_queue = f"test-{uuid.uuid4()}"
    async with Worker(env.client, task_queue=task_queue, workflows=WORKFLOWS,
                      activities=ACTIVITIES):
        first = await run_kyc(env, new_customer("Customer A"), task_queue)
        second = await run_kyc(env, new_customer("Customer B"), task_queue)
        assert (await first.result()).approved
        assert (await second.result()).approved
        steps = await scheduled_activities(first)
        assert [name for name, _ in steps] == [name for name, _ in await scheduled_activities(second)]

    assert steps == [
        ("verify_identity", "KYC 1: verify identity"),
        ("check_proof_of_address", "KYC 2: check proof of address (utility-bill.pdf)"),
        ("screen_sanctions", "KYC 3: screen against sanctions lists"),
        ("record_consent", "KYC 4: record consent to terms 2026-10"),
        ("open_account", "KYC 5: open account (Global Index Fund)"),
    ]


async def test_consent_must_be_typed_exactly(env):
    task_queue = f"test-{uuid.uuid4()}"
    customer = new_customer()
    async with Worker(env.client, task_queue=task_queue, workflows=WORKFLOWS,
                      activities=ACTIVITIES):
        kyc = await env.client.start_workflow(
            KycWorkflow.run, KycInput(customer, "Global Index Fund"),
            id=f"kyc-{customer.customer_id}", task_queue=task_queue,
            execution_timeout=TEST_TIMEOUT,
        )
        await kyc.signal(KycWorkflow.document_uploaded, "utility-bill.pdf")
        await wait_for_stage(kyc, "consent")
        with pytest.raises(WorkflowUpdateFailedError) as rejected:
            await kyc.execute_update(KycWorkflow.give_consent, "ok fine")
        assert rejected.value.cause.message == 'Type "I agree" to accept the terms'
        await kyc.execute_update(KycWorkflow.give_consent, "I agree")
        assert (await kyc.result()).approved


async def test_possible_sanctions_match_waits_for_a_human(env):
    """Fixed does not mean linear: a rule, not the agent, sends this to review."""
    task_queue = f"test-{uuid.uuid4()}"
    customer = new_customer("Alex Smith")
    async with Worker(env.client, task_queue=task_queue, workflows=WORKFLOWS,
                      activities=ACTIVITIES):
        kyc = await env.client.start_workflow(
            KycWorkflow.run, KycInput(customer, "Global Index Fund"),
            id=f"kyc-{customer.customer_id}", task_queue=task_queue,
            execution_timeout=TEST_TIMEOUT,
        )
        await kyc.signal(KycWorkflow.document_uploaded, "utility-bill.pdf")
        await wait_for_stage(kyc, "compliance review")
        await kyc.signal(KycWorkflow.compliance_decision,
                         ComplianceDecision(approved=False, reviewer="Compliance team"))
        result = await kyc.result()

    assert not result.approved
    assert result.steps[-1] == "Compliance review declined by Compliance team"
    # Declined before consent: no account is opened.
    assert "open_account" not in [name for name, _ in await scheduled_activities(kyc)]


# --- The whole hourglass: dynamic, fixed, dynamic again ---


async def run_session(env: WorkflowEnvironment, answers: list[str]) -> tuple[WorkflowHandle, Usage]:
    """Play one customer through a session with the fake model, as the chat would."""
    task_queue = f"test-{uuid.uuid4()}"
    customer = new_customer()
    async with Worker(env.client, task_queue=task_queue, workflows=WORKFLOWS,
                      activities=ACTIVITIES):
        session = await env.client.start_workflow(
            InvestmentSessionWorkflow.run, customer,
            id=f"session-{customer.customer_id}", task_queue=task_queue,
            execution_timeout=TEST_TIMEOUT,
        )
        replies = iter(answers)
        turn = await session.execute_update(InvestmentSessionWorkflow.customer_says, next(replies))
        while turn.expecting != "done":
            if turn.expecting == "message":
                # "no" once the script runs out, so no review timer is booked.
                text = next(replies, "no thanks")
                turn = await session.execute_update(InvestmentSessionWorkflow.customer_says, text)
            elif turn.expecting == "choice":
                turn = await session.execute_update(InvestmentSessionWorkflow.accept_option, 1)
            elif turn.expecting == "kyc":
                await complete_kyc(env.client.get_workflow_handle(f"kyc-{customer.customer_id}"))
                turn = await session.execute_update(InvestmentSessionWorkflow.continue_chat)
        await session.result()
        # Queried while the Worker runs: a Query needs a Worker, even after the run ends.
        usage = await session.query(InvestmentSessionWorkflow.model_usage)
    return session, usage


async def test_session_explores_then_runs_kyc_then_gets_started(env):
    session, usage = await run_session(env, [
        "I'd like to invest £5,000 for 10 years for retirement",
        "I'd be fine, I can ride it out",
        "a one-off amount",
    ])
    tools = [name for name, _ in await scheduled_activities(session)]

    # The dynamic path ends at the handoff, and the agent never opens anything.
    assert "present_options" in tools
    assert "open_account" not in tools  # only the KYC child Workflow can
    assert tools[-1] == "end_conversation"
    history = await session.fetch_history()
    children = [e for e in history.events
                if e.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED]
    assert len(children) == 1

    assert (usage.model_calls, usage.tokens) == (0, 0)  # the fake model is free


async def test_session_history_replays_without_errors(env):
    """Replay a real run's Event History to catch non-deterministic Workflow code."""
    session, _ = await run_session(env, ["I want to save £2,000 for 2 years", "I may need it"])
    history = await session.fetch_history()
    await Replayer(workflows=WORKFLOWS).replay_workflow(history)


# --- Temporal owns the model's retries ---


async def start_session_with_model(env: WorkflowEnvironment, model) -> tuple[WorkflowHandle, str]:
    """Start a session whose decide_next_step is the given stand-in, and send one message."""
    task_queue = f"test-{uuid.uuid4()}"
    customer = new_customer()
    others = [a for a in ACTIVITIES if a is not activities.decide_next_step]
    async with Worker(env.client, task_queue=task_queue, workflows=WORKFLOWS,
                      activities=[model, *others]):
        session = await env.client.start_workflow(
            InvestmentSessionWorkflow.run, customer,
            id=f"session-{customer.customer_id}", task_queue=task_queue,
            execution_timeout=TEST_TIMEOUT,
        )
        await session.start_update(InvestmentSessionWorkflow.customer_says, "Hello",
                                   wait_for_stage=WorkflowUpdateStage.ACCEPTED)
        with pytest.raises(WorkflowFailureError):
            await session.result()
        # Queried while the Worker runs: a Query needs a Worker, even after the run ends.
        usage = await session.query(InvestmentSessionWorkflow.model_usage)
    return session, usage


async def test_failing_model_call_stops_after_three_attempts(env):
    attempts = []

    @activity.defn(name="decide_next_step")
    async def model_that_is_always_rate_limited(phase: str, history: list, final: bool) -> dict:
        attempts.append(activity.info().attempt)
        raise ApplicationError("429 Too Many Requests")  # retryable

    await start_session_with_model(env, model_that_is_always_rate_limited)
    assert attempts == [1, 2, 3]  # capped, so a broken model cannot spend forever


async def test_rejected_api_key_is_not_retried(env):
    attempts = []

    @activity.defn(name="decide_next_step")
    async def model_that_rejects_the_key(phase: str, history: list, final: bool) -> dict:
        attempts.append(activity.info().attempt)
        raise ApplicationError("Incorrect API key provided", non_retryable=True)

    await start_session_with_model(env, model_that_rejects_the_key)
    assert attempts == [1]
