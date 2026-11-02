"""Dataclasses that cross the Temporal boundary.

Temporal serialises these into the Event History, so every field needs a
type hint. Add new fields with defaults so running Workflows keep working.
"""

from dataclasses import dataclass


@dataclass
class Customer:
    """A customer; customer_id also names their Workflows."""

    customer_id: str
    name: str


@dataclass
class KycInput:
    """Input to the KYC child Workflow."""

    customer: Customer
    product: str  # the product the customer accepted


@dataclass
class KycResult:
    """The KYC outcome and the result of each step, in order."""

    approved: bool
    steps: list[str]


@dataclass
class KycProgress:
    """Where the fixed KYC flow has got to, for the chat's checklist."""

    stage: str
    steps: list[str]  # results of the steps completed so far, in order
    flagged: bool  # sent to compliance review by the sanctions rule


@dataclass
class ComplianceDecision:
    """A compliance reviewer's decision on a flagged customer."""

    approved: bool
    reviewer: str


@dataclass
class Message:
    """One chat message shown to the customer."""

    sender: str  # "Agent" or "Timer"
    text: str


@dataclass
class Turn:
    """What the customer sees after each input, and what to send next."""

    messages: list[Message]
    expecting: str  # "message", "choice", "kyc" or "done"
    review_seconds: int | None = None  # set once a review timer is running


@dataclass
class Usage:
    """What the model cost this session: successful calls and their tokens."""

    model_calls: int = 0
    tokens: int = 0
