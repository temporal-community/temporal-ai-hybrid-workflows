"""Activities: the agent's model call, its tools, and the fixed KYC steps.

Everything that touches the outside world runs here, not in the Workflow.
Temporal records each result in the Event History, so after a crash a new
Worker reuses finished steps instead of running them again. Every tool and
KYC step is a stub that returns made-up data.
"""

import os

from temporalio import activity

from app.agent import openai_decide
from app.fake_model import fake_decide
from app.models import KycInput
from app.products import PRODUCTS

RISK_ORDER = ["low", "medium", "high"]

# --- The agent's "brain": one model call that picks the next tool. ---


@activity.defn
async def decide_next_step(phase: str, history: list[dict], final_turn: bool) -> dict:
    """Pick the next tool: {"tool": ..., "args": ..., "usage": {"model_calls", "tokens"}}."""
    # The fake model is the default, so the demo runs with no key and no cost.
    # Only USE_FAKE_MODEL=0 calls OpenAI.
    if os.environ.get("USE_FAKE_MODEL", "1") != "0":
        decision = fake_decide(phase, history, final_turn)
        return {**decision, "usage": {"model_calls": 0, "tokens": 0}}  # no model, no cost
    return await openai_decide(phase, history, final_turn)


# --- Agent tools. Before KYC the agent can only look and talk; only the    ---
# --- fixed KYC flow opens the account. Afterwards it can make small,       ---
# --- checked changes (maturity instruction, review date).                 ---


@activity.defn
async def ask_customer(args: dict) -> str:
    """Pass the agent's question on; the Workflow then waits for the reply."""
    # The question reaches the customer through the Workflow's reply; a real
    # system might also push it to a chat channel or app here.
    return "Question sent"


@activity.defn
async def present_options(args: dict) -> str:
    """End the explore phase with one to three options for the customer."""
    return f"Presented {len(args['options'])} options"


@activity.defn
async def search_products(args: dict) -> list[dict]:
    """Return up to three products that fit the risk, horizon and access needs."""
    max_risk = RISK_ORDER.index(args["risk_level"])
    matches = [
        p for p in PRODUCTS
        if RISK_ORDER.index(p["risk"]) <= max_risk
        and p["min_years"] <= args["horizon_years"]
        and (p["easy_access"] or not args["needs_access"])
    ]
    matches.sort(key=lambda p: (RISK_ORDER.index(p["risk"]), p["min_years"]), reverse=True)
    return [{"id": p["id"], "name": p["name"], "isa": p["isa"]} for p in matches[:3]]


@activity.defn
async def get_product_details(args: dict) -> dict:
    """Return the full description, risk, fees and access of one product."""
    return next(p for p in PRODUCTS if p["id"] == args["product_id"])


ISA_ALLOWANCE = {"annual_allowance": 20_000, "used_this_tax_year": 4_000, "remaining": 16_000}


@activity.defn
async def check_isa_allowance(args: dict) -> dict:
    """Return the customer's remaining ISA allowance for this tax year."""
    return ISA_ALLOWANCE


@activity.defn
async def get_account_summary(args: dict) -> dict:
    """Return what the customer holds, its risk and fees, and whether it takes top-ups."""
    product = next(p for p in PRODUCTS if p["id"] == args["product_id"])
    return {"product": product["name"], "status": "open", "risk": product["risk"],
            "fees": product["fees"], "isa": product["isa"],
            "allows_top_ups": product["allows_top_ups"]}


@activity.defn
async def suggest_contribution_plan(args: dict) -> dict:
    """Show a year of payments, with an ISA-limit warning checked in code."""
    amount, monthly = args["amount"], args["frequency"] == "monthly"
    product = next(p for p in PRODUCTS if p["id"] == args["product_id"])
    plan = {
        "amount": amount,
        "frequency": args["frequency"],
        "total_first_year": amount * 12 if monthly else amount,
        "note": "Regular payments spread your buying over time, which smooths out ups and downs."
        if monthly else
        "A one-off payment is invested straight away, so it is exposed to rises and falls straight away.",
    }
    # An exact rule, so it is checked in code rather than left to the model.
    remaining = ISA_ALLOWANCE["remaining"]
    if product["isa"] and plan["total_first_year"] > remaining:
        plan["warning"] = (f"£{plan['total_first_year']:,.0f} this year is over the remaining "
                           f"ISA allowance of £{remaining:,}. Tell the customer.")
    return plan


@activity.defn
async def set_maturity_instruction(args: dict) -> str:
    """Record what happens when a fixed-term product ends."""
    return f"At the end of the term: {args['option']}"


@activity.defn
async def send_guide(args: dict) -> str:
    """Send the customer a short plain-English guide."""
    return f"Guide sent: {args['topic']}"


@activity.defn
async def schedule_review(args: dict) -> str:
    """Confirm a review booking; the Workflow's durable timer does the waiting."""
    # The Workflow holds the review as a durable timer; this just confirms it.
    return f"Review booked in {args['months']} months"


@activity.defn
async def end_conversation(args: dict) -> str:
    """Send the closing message that ends a phase."""
    return "Message sent"


# --- KYC steps. Stubbed: a real system would call identity, address and ---
# --- sanctions providers here.                                           ---


@activity.defn
async def verify_identity(kyc: KycInput) -> str:
    """Check the customer is who they say they are (stub: always passes)."""
    return f"Identity verified for {kyc.customer.name}"


@activity.defn
async def check_proof_of_address(kyc: KycInput, document: str) -> str:
    """Check the proof-of-address document (stub: always accepts it)."""
    return f"Proof of address accepted: {document}"


@activity.defn
async def screen_sanctions(kyc: KycInput) -> dict:
    """Screen the customer against sanctions lists (stub: flags any Smith)."""
    # Mock: common surnames give a possible match, as real screening often does.
    if "smith" in kyc.customer.name.lower():
        return {"possible_match": True,
                "summary": f"Possible sanctions match for {kyc.customer.name}: sent for review"}
    return {"possible_match": False, "summary": f"No sanctions match for {kyc.customer.name}"}


@activity.defn
async def record_consent(kyc: KycInput, terms_version: str, typed: str) -> str:
    """Record the customer's consent, with the exact terms version they agreed to."""
    return f'{kyc.customer.name} agreed to terms {terms_version} for {kyc.product}: "{typed}"'


@activity.defn
async def open_account(kyc: KycInput) -> str:
    """Open the account. Only the fixed KYC flow can reach this step."""
    return f"Account opened: {kyc.product} for {kyc.customer.name}"
