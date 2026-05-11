# compiled-skill-dag

Tiny demo of one idea:

> Let the graph own control flow. Let the LLM reason inside the box.

Prompt-planned agents can do weird things. They can call tools in the wrong order, skip prerequisite evidence, or decide they are done before the workflow is actually valid.

This repo shows the opposite pattern.

The execution path is compiled into a small runtime DAG. The LLM only runs inside the `diagnose` node. It sees finished artifacts. It does not get tool handles. It cannot reorder the graph.

This does not solve hallucination. The model can still write a bad diagnosis.

It does remove one class of failure: **control-flow hallucination**.

## The example

We investigate a fake exception spike.

The runtime does exactly this:

```
get_trend
  -> get_stack
  -> search_source
  -> diagnose
  -> gate_claim
```

Three mock tools exist:

```
telemetry.get_exception_trend
telemetry.get_stack_sample
source.search_symbol
```

Only `runner.py` can call them. The LLM cannot.

## Why this matters

In a prompt-planned agent, the model might say:

```
I'll search the source first.
> source.search_symbol("HandleRequest")
```

But source search needs the top stack frame. No stack, no symbol. Wrong order.

In the DAG version, that call is impossible. `search_source` has a dependency on `get_stack` and a `requires` check on `stack_packet.top_frame`. If the stack is missing, source search is skipped and the deterministic gate emits:

```json
{ "verdict": "blocked_missing" }
```

No crash. No fake confidence. No invented control flow.

## Layout

```
.
├── skill/
│   ├── SKILL.md
│   └── references/runtime_dag.json
├── tools/
│   ├── telemetry.py
│   └── source.py
├── fixtures/
│   ├── good_case.json
│   ├── missing_stack.json
│   └── bad_prompt_baseline.txt
└── runner.py
```

Stdlib only. No `pip install` needed.

## LLM provider

The `diagnose` node optionally calls [OpenRouter](https://openrouter.ai). To enable:

```bash
export OPENROUTER_API_KEY=sk-or-...
# optional — defaults to anthropic/claude-haiku-4.5
export OPENROUTER_MODEL=anthropic/claude-haiku-4.5
```

Without `OPENROUTER_API_KEY`, or if the request fails, the node falls back to a deterministic stub and prints a one-line warning to stderr. Either way the gate's verdict is unchanged — the control-flow claim does not depend on which path produced the label.

The artifact records which path ran:

```json
{ "source": "stub" }
{ "source": "openrouter:anthropic/claude-haiku-4.5" }
```

## Run it

```bash
# All three at once, with assertions:
python runner.py test
```

Or one at a time.

**Good case** — trend, stack, and source evidence all exist.

```bash
python runner.py run good_case.json
cat out/claim.json
```

```json
{
  "verdict": "supported",
  "label": "likely_null_deref",
  "summary": "NullReferenceException spiked 17.75x (142 in 1h).",
  "top_frame": "OrderService.HandleRequest"
}
```

**Missing-stack case** — trend exists, stack does not.

```bash
python runner.py run missing_stack.json
cat out/claim.json
```

```json
{
  "verdict": "blocked_missing",
  "missing": ["source_packet", "stack_packet"],
  "details": { "stack_packet": "missing", "source_packet": "skipped" }
}
```

**Baseline transcript** — a prompt-planned agent calls source search before stack retrieval.

```bash
python runner.py validate fixtures/bad_prompt_baseline.txt
```

```json
{
  "errors": 1,
  "first": "source_search_before_stack",
  "all": ["source_search_before_stack"]
}
```

## What this proves

Prompt-planned agents can choose invalid control flow.
A compiled DAG makes that class of error impossible by construction.

It does not prove:

- LLMs stop hallucinating
- diagnoses are always correct
- real MCP failures are handled
- production agent orchestration is solved

This is just the small core.

The graph owns the path.
The model reasons inside one node.
The gate decides whether the result is admissible.

That is the pattern.
