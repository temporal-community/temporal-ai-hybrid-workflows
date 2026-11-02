# Why KYC always runs (and why the agent can't skip it)

A pivotal point for the video. The obvious question is: "If the agent
chooses the path, how do we make sure it runs KYC? Is that in the prompt?"
It sounds circular, but it isn't.

## Short answer
The agent never runs KYC, and it can't. KYC isn't in the prompt and isn't
one of the agent's tools. It's ordinary Workflow code that runs after the
agent loop returns. The agent is a step inside the Workflow, not the thing
in charge of it.

## Who controls what
The order is set in `InvestmentSessionWorkflow.run` (`app/workflows.py`):

```python
proposal = await self.run_agent("explore")       # agent decides, inside a box
...
await workflow.wait_condition(lambda: self.accepted is not None)   # customer decides
kyc = await workflow.start_child_workflow(KycWorkflow.run, ...)    # code decides
```

- **The agent** controls only what happens inside `run_agent`: which
  questions to ask, which tools to call, in what order, and when to stop by
  calling `present_options`. It can't skip KYC, reorder it or start it
  early, because it has no tool that touches it (`grep -i kyc app/agent.py`
  finds nothing).
- **The customer** controls whether to accept an option (the
  `accept_option` Update).
- **The Workflow code** controls everything else. Once a product is
  accepted, KYC always runs. That's a line of Python, not an instruction
  the model might ignore.

## The real question: does the agent loop always end?
"How do we ensure the agent runs KYC" becomes "how do we make sure the
agent loop always ends". There are three mechanisms for that, none of them
in the prompt:

1. **A turn limit.** `MAX_TURNS` in `app/agent.py` bounds the loop.
2. **A forced last call.** On the final turn, `tool_choice` makes the model
   call `present_options` (`openai_decide` in `app/agent.py`). The prompt
   also asks it to finish that way, but the prompt is just a nudge. The code
   is what guarantees it.
3. **Validation.** `check_tool_call` rejects bad handoffs, such as products
   that never came back from a search, or fewer than one or more than three
   options.

## One line for the video
> The prompt shapes how the agent explores. The code decides what happens
> next. The agent never decides whether KYC runs; it only decides when it
> has finished exploring.

This is the "agent is the driver, the workflow is the road network"
metaphor. The driver picks the route across the open part, but the bridge
is the only way over. Nobody has to ask the driver to use it.

## The precise guarantee, and one gap
The guarantee is **"if a product is accepted, KYC runs, in fixed order"**,
not "KYC always runs". Two paths correctly skip it:

- **No acceptable proposal.** If `present_options` fails validation on the
  final turn, the loop ends, `run_agent` returns `options: []`, and the
  session ends with "Sorry…". Nothing was chosen, so there's nothing to
  onboard. That's the right behaviour, but it's a quiet failure. If you'd
  rather hit that path rarely, one option is to retry the final turn a
  couple of times with the validation error before giving up. The trade-off
  is a slightly longer loop.
- **The customer never accepts.** The Workflow waits indefinitely, which is
  durable and correct, but KYC never starts.

Both fit the demo's framing: Temporal guarantees that the chosen path
runs durably and is recorded. It doesn't guarantee that the agent reaches a
good proposal.
