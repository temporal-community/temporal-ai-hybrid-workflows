"""The Workflows: the agent session (dynamic) and KYC (fixed).

InvestmentSessionWorkflow runs the agent loop, hands off to the customer,
starts the KYC child Workflow, then runs the agent loop again. KycWorkflow
runs the same checks in the same order for every customer. Workflow code
must be deterministic; anything non-deterministic happens in an Activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app import activities
    from app.agent import MAX_TURNS, PHASES, check_tool_call, disclaimer
    from app.models import (
        ComplianceDecision, Customer, KycInput, KycProgress, KycResult, Message, Turn, Usage,
    )

TASK_QUEUE = "investment-demo"
TIMEOUT = timedelta(seconds=30)
# The default retry policy retries forever, and every attempt at a model call
# can cost tokens. Three attempts ride out a rate limit or a blip, then stop.
MODEL_RETRY = RetryPolicy(maximum_attempts=3)
DEMO_SECONDS_PER_MONTH = 2  # a 12-month review fires after 24 seconds

# Fixed wording: every customer sees exactly this text, and agrees to this version.
TERMS_VERSION = "2026-10"
TERMS = """\
Terms for opening your account (version 2026-10, demo only)
  1. You chose this product yourself. We gave you general guidance, not a
     personal recommendation.
  2. If you chose an investment, its value can go down as well as up, and
     you may get back less than you put in.
  3. You can cancel within 14 days of opening the account."""

# Every tool the agent could call. Each phase allows only some of them.
TOOLS = {
    "ask_customer": activities.ask_customer,
    "search_products": activities.search_products,
    "get_product_details": activities.get_product_details,
    "check_isa_allowance": activities.check_isa_allowance,
    "present_options": activities.present_options,
    "get_account_summary": activities.get_account_summary,
    "suggest_contribution_plan": activities.suggest_contribution_plan,
    "set_maturity_instruction": activities.set_maturity_instruction,
    "send_guide": activities.send_guide,
    "schedule_review": activities.schedule_review,
    "end_conversation": activities.end_conversation,
}


@workflow.defn
class InvestmentSessionWorkflow:
    """Dynamic, then fixed, then dynamic again: the hourglass."""

    def __init__(self) -> None:
        self.history: list[dict] = []
        self.messages: list[Message] = []
        self.expecting: str | None = "message"
        self.wait_points = 0
        self.delivered_points = 0
        self.delivered_messages = 0
        self.customer_message: str | None = None
        self.options: list[dict] = []
        self.accepted: dict | None = None
        self.review_months: int | None = None
        self.review_seconds: int | None = None
        self.usage = Usage()

    @workflow.run
    async def run(self, customer: Customer) -> str | None:
        """Explore, hand off, run KYC, get started, then the review."""
        # 1. Dynamic: the agent explores options with the customer.
        await self.wait_for_customer(opening=True)
        proposal = await self.run_agent("explore")
        self.options = proposal["options"]
        if not self.options:
            return await self.finish("Sorry, I couldn't find suitable options this time.")
        self.post("Agent", format_options(proposal))

        # 2. The handoff: the customer, not the agent, makes the choice.
        self.wait_point("choice", "Waiting for the customer to choose an option")
        await workflow.wait_condition(lambda: self.accepted is not None)
        self.expecting = None

        # 3. Fixed: the same KYC child Workflow for every customer.
        product = self.accepted
        kyc = await workflow.start_child_workflow(
            KycWorkflow.run,
            KycInput(customer, product["name"]),
            id=f"kyc-{customer.customer_id}",
            static_summary=f"Fixed KYC and account opening: {product['name']}",
        )
        self.wait_point("kyc", "Waiting for KYC and account opening")
        result: KycResult = await kyc
        if not result.approved:
            return await self.finish("Sorry, we weren't able to open your account this time.")

        # 4. Dynamic again: the agent helps the customer get started.
        self.note(f"KYC is complete. The customer's {product['name']} account "
                  f"(product_id: {product['product_id']}) is now open.")
        closing = await self.run_agent("get_started")
        if self.review_months is None:
            return await self.finish(closing["message"])
        self.post("Agent", closing["message"])

        # 5. Durable timer: wait for the review, even across crashes and redeploys.
        seconds = self.review_seconds = self.review_months * DEMO_SECONDS_PER_MONTH
        await self.finish(f"Review booked in {self.review_months} months "
                          f"(demo: {seconds} seconds).", sender="Timer")
        workflow.set_current_details("Waiting for the customer's review")
        await workflow.sleep(
            timedelta(seconds=seconds),
            summary=f"Wait {self.review_months} months for the customer's review",
        )
        self.note(f"{self.review_months} months have passed. The customer's review is due. "
                  f"Their account is the {product['name']} (product_id: {product['product_id']}).")
        review = await self.run_agent("review")
        return review["message"]

    async def run_agent(self, phase: str) -> dict:
        """The agent loop. Returns the arguments of the tool that ends the phase."""
        last_tool = PHASES[phase]["last_tool"]
        for turn in range(MAX_TURNS):
            final_turn = turn == MAX_TURNS - 1
            decision = await workflow.execute_activity(
                activities.decide_next_step, args=[phase, self.history, final_turn],
                start_to_close_timeout=TIMEOUT,
                retry_policy=MODEL_RETRY,
                summary=f"{phase}, turn {turn + 1}: agent decides"
                + (f" (must call {last_tool})" if final_turn else ""),
            )
            used = decision.pop("usage")
            self.usage.model_calls += used["model_calls"]
            self.usage.tokens += used["tokens"]
            self.history.append({"role": "agent", **decision})
            tool, args = decision["tool"], decision["args"]

            # Code constrains: a call that breaks the rules goes back to the agent.
            error = check_tool_call(phase, tool, args, self.history)
            if error:
                self.history.append({"role": "tool", "tool": tool, "result": error})
                continue

            result = await workflow.execute_activity(
                TOOLS[tool], args, start_to_close_timeout=TIMEOUT, summary=describe(tool, args)
            )
            self.history.append({"role": "tool", "tool": tool, "result": result})

            if tool == "ask_customer":
                self.post("Agent", args["question"])
                await self.wait_for_customer()
            elif tool == "schedule_review":
                self.review_months = args["months"]
            elif tool == last_tool:
                return args
        return {"options": [], "message": "Sorry, I've run out of time for this conversation."}

    @workflow.query
    def model_usage(self) -> Usage:
        """Model calls and tokens so far, so the cost is measured, not guessed."""
        return self.usage

    # --- Talking to the customer ---

    @workflow.update
    async def customer_says(self, text: str) -> Turn:
        """The customer types a message; returns what they see next."""
        self.customer_message = text
        return await self.next_turn()

    @customer_says.validator
    def validate_message(self, text: str) -> None:
        """Reject a message the agent isn't waiting for, before it reaches the history."""
        if self.expecting != "message":
            raise ValueError("The agent isn't waiting for a message right now")

    @workflow.update
    async def accept_option(self, choice: int) -> Turn:
        """The customer picks one of the presented options by number."""
        self.accepted = self.options[choice - 1]
        return await self.next_turn()

    @accept_option.validator
    def validate_choice(self, choice: int) -> None:
        """Reject a choice that isn't expected or isn't on the list."""
        # Rejected before it reaches the Workflow history.
        if self.expecting != "choice":
            raise ValueError("There is no choice to make right now")
        if not 1 <= choice <= len(self.options):
            raise ValueError(f"Choose a number from 1 to {len(self.options)}")

    @workflow.update
    async def continue_chat(self) -> Turn:
        """Called by the chat once KYC is done; returns what the customer sees next."""
        return await self.next_turn()

    async def next_turn(self) -> Turn:
        """Wait until the Workflow needs the customer, then return the new messages."""
        # Wait until the Workflow next needs the customer, then hand over
        # everything posted since the last turn.
        await workflow.wait_condition(lambda: self.wait_points > self.delivered_points)
        self.delivered_points = self.wait_points
        messages = self.messages[self.delivered_messages:]
        self.delivered_messages = len(self.messages)
        return Turn(messages, self.expecting, self.review_seconds)

    def wait_point(self, expecting: str, details: str) -> None:
        """Mark that the Workflow is now waiting for the customer."""
        self.expecting = expecting
        self.wait_points += 1
        workflow.set_current_details(details)

    async def wait_for_customer(self, opening: bool = False) -> None:
        """Wait for the customer's next message and add it to the history."""
        if not opening:  # the chat already starts on the customer's turn
            self.wait_point("message", "Waiting for the customer to reply")
        await workflow.wait_condition(lambda: self.customer_message is not None)
        self.history.append({"role": "customer", "text": self.customer_message})
        self.customer_message = None
        self.expecting = None

    async def finish(self, text: str, sender: str = "Agent") -> None:
        """Post the last message and wait for the chat to collect it."""
        self.post(sender, text)
        self.wait_point("done", "Conversation finished")
        try:  # give the chat a moment to collect the last messages
            await workflow.wait_condition(
                lambda: self.delivered_points == self.wait_points,
                timeout=timedelta(minutes=1),
                timeout_summary="Wait for the chat to collect the last messages",
            )
        except TimeoutError:
            pass
        await workflow.wait_condition(workflow.all_handlers_finished)

    def post(self, sender: str, text: str) -> None:
        """Add a message for the customer to see on their next turn."""
        self.messages.append(Message(sender, text))

    def note(self, text: str) -> None:
        """A fact for the agent, such as the account being opened."""
        self.history.append({"role": "note", "text": text})


def describe(tool: str, args: dict) -> str:
    """One-line label for the Temporal UI, so the history reads as the agent's path."""
    match tool:
        case "ask_customer":
            return f"Ask customer: {args['question']}"
        case "search_products":
            access = ", easy access" if args["needs_access"] else ""
            return f"Search products: {args['risk_level']} risk, {args['horizon_years']} years{access}"
        case "get_product_details":
            return f"Product details: {args['product_id']}"
        case "check_isa_allowance":
            return "Check remaining ISA allowance"
        case "present_options":
            return "Present options: " + ", ".join(o["name"] for o in args["options"])
        case "get_account_summary":
            return f"Account summary: {args['product_id']}"
        case "suggest_contribution_plan":
            return f"Contribution plan: £{args['amount']:,.0f} {args['frequency']}"
        case "set_maturity_instruction":
            return f"At end of term: {args['option']}"
        case "send_guide":
            return f"Send guide: {args['topic']}"
        case "schedule_review":
            return f"Schedule review in {args['months']} months"
        case "end_conversation":
            return "End conversation"
    return tool


def format_options(args: dict) -> str:
    """Format the presented options and the matching risk wording."""
    lines = [args["explanation"]]
    lines += [f"  {i}. {o['name']}" for i, o in enumerate(args["options"], 1)]
    lines.append(disclaimer([o["product_id"] for o in args["options"]]))
    return "\n".join(lines)


@workflow.defn
class KycWorkflow:
    """The fixed part. Same checks, same order, every customer.

    It can wait for days (a document, a human reviewer) and resumes exactly
    where it stopped, even if every worker restarts in between.
    """

    def __init__(self) -> None:
        self.stage = "starting"
        self.steps: list[str] = []
        self.flagged = False
        self.document: str | None = None
        self.decision: ComplianceDecision | None = None
        self.consent: str | None = None

    @workflow.run
    async def run(self, kyc: KycInput) -> KycResult:
        """Run the fixed checks in order, waiting for the customer and reviewer."""
        steps = self.steps
        self.set_stage("identity check")
        steps.append(await workflow.execute_activity(
            activities.verify_identity, kyc, start_to_close_timeout=TIMEOUT,
            summary="KYC 1: verify identity"))

        self.set_stage("proof of address")  # the document may arrive days later
        await workflow.wait_condition(lambda: self.document is not None)
        steps.append(await workflow.execute_activity(
            activities.check_proof_of_address, args=[kyc, self.document],
            start_to_close_timeout=TIMEOUT, summary=f"KYC 2: check proof of address ({self.document})"))

        self.set_stage("sanctions screening")
        screening = await workflow.execute_activity(
            activities.screen_sanctions, kyc, start_to_close_timeout=TIMEOUT,
            summary="KYC 3: screen against sanctions lists")
        steps.append(screening["summary"])

        # Fixed does not mean linear: a rule written in advance, not the agent,
        # sends a possible match to a human.
        if screening["possible_match"]:
            self.flagged = True
            self.set_stage("compliance review")
            await workflow.wait_condition(lambda: self.decision is not None)
            outcome = "approved" if self.decision.approved else "declined"
            steps.append(f"Compliance review {outcome} by {self.decision.reviewer}")
            if not self.decision.approved:
                self.set_stage("declined")
                return KycResult(False, steps)

        self.set_stage("consent")
        await workflow.wait_condition(lambda: self.consent is not None)
        steps.append(await workflow.execute_activity(
            activities.record_consent, args=[kyc, TERMS_VERSION, self.consent],
            start_to_close_timeout=TIMEOUT, summary=f"KYC 4: record consent to terms {TERMS_VERSION}"))

        self.set_stage("opening account")
        steps.append(await workflow.execute_activity(
            activities.open_account, kyc, start_to_close_timeout=TIMEOUT,
            summary=f"KYC 5: open account ({kyc.product})"))
        self.set_stage("done")
        return KycResult(True, steps)

    @workflow.query
    def progress(self) -> KycProgress:
        """Where KYC has got to, for the chat's checklist."""
        return KycProgress(self.stage, self.steps, self.flagged)

    @workflow.signal
    def document_uploaded(self, file_name: str) -> None:
        """Receive the proof-of-address document, however late it arrives."""
        self.document = file_name

    @workflow.signal
    def compliance_decision(self, decision: ComplianceDecision) -> None:
        """Receive the compliance reviewer's decision."""
        self.decision = decision

    @workflow.update
    def give_consent(self, typed: str) -> str:
        """Record the customer's typed consent."""
        self.consent = typed
        return "Consent recorded"

    @give_consent.validator
    def validate_consent(self, typed: str) -> None:
        """Accept consent only at the consent step, and only as "I agree"."""
        if self.stage != "consent":
            raise ValueError("Consent isn't needed yet")
        if typed.strip().lower() != "i agree":
            raise ValueError('Type "I agree" to accept the terms')

    def set_stage(self, stage: str) -> None:
        """Update the stage shown by the Query and in the Temporal Web UI."""
        self.stage = stage
        workflow.set_current_details(f"KYC: {stage}")
