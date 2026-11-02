"""The customer's chat: starts a session and talks to it through Updates.

Run it with `make run NAME="Customer A"`. Each message is a Temporal Update,
so the Workflow records it and replies with what the customer sees next.
"""

import asyncio
import sys

from temporalio.client import Client, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from app import ui
from app.models import Customer, Turn
from app.workflows import TASK_QUEUE, TERMS, InvestmentSessionWorkflow, KycWorkflow


async def main() -> None:
    """Start a session for the named customer and chat until it is done."""
    name = sys.argv[1] if len(sys.argv) > 1 else "Customer A"
    customer = Customer(name.lower().replace(" ", "-"), name)

    client = await Client.connect("localhost:7233")
    session = await client.start_workflow(
        InvestmentSessionWorkflow.run,
        customer,
        id=f"session-{customer.customer_id}",
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,  # easy retakes
    )
    ui.note("Demo only, with made-up products. The agent gives general guidance, not\n"
            "financial advice (UK wording; rules differ elsewhere).\n")
    ui.note("Tell the agent what you'd like to do with your money.\n")

    turn = Turn([], "message")
    while turn.expecting != "done":
        try:
            if turn.expecting == "message":
                text = ui.ask(name)
                with ui.thinking():
                    turn = await session.execute_update(InvestmentSessionWorkflow.customer_says, text)
            elif turn.expecting == "choice":
                # The handoff: the customer chooses; the agent cannot.
                choice = int(ui.ask("Which option would you like? (number)"))
                with ui.thinking():
                    turn = await session.execute_update(InvestmentSessionWorkflow.accept_option, choice)
            elif turn.expecting == "kyc":
                await kyc_steps(client.get_workflow_handle(f"kyc-{customer.customer_id}"), customer)
                with ui.thinking():
                    turn = await session.execute_update(InvestmentSessionWorkflow.continue_chat)
        except ValueError:
            ui.error("Please type a number.")
            continue
        except WorkflowUpdateFailedError as err:
            ui.error(err.cause.message)
            continue
        for message in turn.messages:
            ui.say(message.sender, message.text)

    # If a review was booked, the Workflow is now sleeping on a durable timer.
    if turn.review_seconds:
        ui.say("Agent", await ui.countdown(turn.review_seconds, session.result()))
    else:
        await session.result()

    # Measured, not guessed: what the model cost this session.
    usage = await session.query(InvestmentSessionWorkflow.model_usage)
    if usage.model_calls:
        ui.note(f"Model: {usage.model_calls} calls, {usage.tokens:,} tokens.")
    else:
        ui.note("Model: fake. No model calls.")


async def kyc_steps(kyc: WorkflowHandle, customer: Customer) -> None:
    """Follow the fixed KYC Workflow, prompting the customer when it needs them."""
    panel = ui.KycPanel()
    seen = set()
    while True:
        try:
            progress = await kyc.query(KycWorkflow.progress)
        except RPCError:
            await asyncio.sleep(1)  # no worker running right now; keep waiting
            continue
        if progress != panel.progress:
            panel.show(progress)
        stage = progress.stage
        if stage in ("done", "declined"):
            return
        if stage not in seen:
            seen.add(stage)
            if stage == "proof of address":
                document = panel.ask("Proof of address (file name, or Enter to send it later)")
                if document.strip():
                    await kyc.signal(KycWorkflow.document_uploaded, document.strip())
                else:
                    panel.note(stage, "Waiting for your document. Send it later with:\n"
                               f"  uv run python -m app.upload {customer.customer_id} <file>")
            elif stage == "compliance review":
                panel.note(stage, "Needs a quick check by our compliance team. Reviewer:\n"
                           f"  uv run python -m app.review {customer.customer_id} approve|decline")
            elif stage == "consent":
                await give_consent(kyc, panel)
        await asyncio.sleep(0.5)


async def give_consent(kyc: WorkflowHandle, panel: ui.KycPanel) -> None:
    """Show the fixed terms and send what the customer types as an Update."""
    panel.note("consent", TERMS)
    prompt = 'Type "I agree" to accept these terms'
    while True:
        typed = panel.ask(prompt)
        try:
            await kyc.execute_update(KycWorkflow.give_consent, typed)
            return
        except WorkflowUpdateFailedError as err:
            prompt = err.cause.message


if __name__ == "__main__":
    asyncio.run(main())
