"""The Worker: runs the Workflows and Activities for this demo.

Run it with `make worker` (fake model by default, or as set in .env), or
`make worker REAL=1` for OpenAI.
"""

import asyncio

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from app import activities
from app.workflows import TASK_QUEUE, InvestmentSessionWorkflow, KycWorkflow


# Every Activity this Worker can run: the agent's model call, its tools, and
# the fixed KYC steps.
ACTIVITIES = [
    activities.decide_next_step,
    activities.ask_customer,
    activities.search_products,
    activities.get_product_details,
    activities.check_isa_allowance,
    activities.present_options,
    activities.get_account_summary,
    activities.suggest_contribution_plan,
    activities.set_maturity_instruction,
    activities.send_guide,
    activities.schedule_review,
    activities.end_conversation,
    activities.verify_identity,
    activities.check_proof_of_address,
    activities.screen_sanctions,
    activities.record_consent,
    activities.open_account,
]


async def main() -> None:
    """Connect to Temporal and poll the Task Queue until stopped."""
    load_dotenv()  # OPENAI_API_KEY, OPENAI_MODEL, USE_FAKE_MODEL
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[InvestmentSessionWorkflow, KycWorkflow],
        activities=ACTIVITIES,
    )
    print(f"Worker listening on '{TASK_QUEUE}'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
