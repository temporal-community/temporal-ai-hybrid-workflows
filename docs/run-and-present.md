# Run and present the demo

How to run the demo end to end and what to point at in the Temporal Web UI.
The README covers setup; this guide is for presenting it live or trying the
different paths yourself.

## Before you start

```bash
make server                  # terminal 1, Temporal Web UI at http://localhost:8233
make worker REAL=1           # terminal 2 (OpenAI; plain `make worker` uses the fake model)
make run NAME="Customer A"   # terminal 3, one chat per customer
                             # terminal 4: make upload / make review
```

- With the real model, the agent's questions vary from run to run. Answer
  whatever it asks in the spirit of the customer. The answers below are the
  ones that steer the path.
- If the OpenAI API misbehaves mid-session, restart the Worker with plain
  `make worker` to switch to the fake model.
- A full session takes about 45 seconds of machine time plus typing.
- Running the same name again replaces that customer's previous session.
  `make reset` clears every running session.

## Customer A: the hourglass end to end

| When the agent asks about | Type |
|---|---|
| Opening | `Hi, I have £10,000 I'd like to invest` |
| Purpose / horizon | `It's for retirement, in about 15 years` |
| Ups and downs | `I'd be fine, I can ride them out` |
| Choice | `1` (Global Shares Index Fund) |
| Proof of address | `utility-bill.pdf` |
| Terms | `I agree` |
| One-off or monthly | `Monthly please`, then `£200 a month` |
| Review | `Yes please` |

In the Web UI, open `session-customer-a`:
- At the top, the agent's path: its questions, the search, and the product
  details, each one an Activity.
- The `kyc-customer-a` child Workflow, with steps `KYC 1` to `KYC 5`.
- After KYC, the contribution plan and the review booking.
- The timer "Wait 12 months for the customer's review" fires after 24
  seconds, then the agent checks in.

## Customer B: same bridge, different road

| When the agent asks about | Type |
|---|---|
| Opening | `I've got £8,000 saved for a house deposit` |
| Horizon | `About 2 years` |
| Access | `I might need it at short notice, and I'd like it tax-free` |
| Choice | the Easy Access Cash ISA |
| Proof of address / terms | `council-tax-bill.pdf` / `I agree` |
| One-off or monthly | `Monthly, £1,500 a month` |

Point at:
- The ISA allowance check during the explore phase.
- `kyc-customer-b` next to `kyc-customer-a`: the steps are identical.
- After KYC, the warning that £18,000 a year is over the £16,000 remaining
  allowance. The warning comes from code in `suggest_contribution_plan`, not
  from the model. Exact rules live in code; the tool returns the warning and
  the model passes it on.

## Customer C: a path with no payment questions

| When the agent asks about | Type |
|---|---|
| Opening / horizon | `I've got £8,000 for a house deposit, in about 2 years` |
| Access | `It can be locked away, I won't need it before then` |
| Choice | the 2-Year Fixed Saver |
| Proof of address / terms | `bank-statement.pdf` / `I agree` |
| End of term | `Pay it out to my bank` |

Point at: after KYC there's no contribution plan. The product can't take
more payments, so the agent asks what should happen at the end of the term
instead.

## Alex Smith: durability and the human in the loop

| Step | Do |
|---|---|
| Terminal 3 | `make run NAME="Alex Smith"`, have a short conversation, and pick any option |
| Proof of address | Press **Enter** to send it later |
| Terminal 4 | `make failure`. The Worker is killed; `kyc-alex-smith` is still waiting in the Web UI |
| Terminal 2 | `make recovery REAL=1` (or plain `make recovery` for the fake model), then wait 5 to 10 seconds |
| Terminal 4 | `make upload ID=alex-smith FILE=council-tax-bill.pdf` |
| Terminal 3 | Wait for "Needs a quick check by our compliance team" |
| Terminal 4 | `make review ID=alex-smith VERDICT=approve` (only after that line appears) |
| Terms | `I agree` |

Point at:
- In `kyc-alex-smith`, identity was checked once, before the crash, and not
  again.
- The compliance review branch. A rule written in advance (any name with
  "Smith" gives a possible sanctions match) sends it there, not the agent.

Variation: `VERDICT=decline` ends the session politely without opening an
account.

## Claims to keep precise

- Durable execution guarantees that every step is recorded, retried, and
  resumable. It doesn't guarantee that the agent chose well; evals and
  guardrails do that. See [why KYC always runs](why-kyc-always-runs.md).
- The agent gives general guidance, not a personal recommendation. This is
  UK wording (the FCA's advice vs guidance line); rules differ in other
  jurisdictions.
- Rejected tool calls (wrong phase, a product that wasn't searched for, an
  assumed amount) show up in the Event History: the agent chooses, code
  constrains.
