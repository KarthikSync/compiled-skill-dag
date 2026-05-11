# compiled-skill-dag

A five-minute demo of one narrow claim:

> **Prompt-planned agents can choose invalid control flow; a compiled DAG
> makes that class of error impossible by construction.**

This demo addresses **control-flow hallucination only**, not reasoning
hallucination. The LLM inside `diagnose` can still produce a wrong label —
that is out of scope. What the DAG eliminates is the model's freedom to
reorder stages, skip dependencies, or call a tool it shouldn't.

## Layout

```
.
├── skill/
│   ├── SKILL.md
│   └── references/runtime_dag.yaml   # the five-node graph
├── tools/
│   ├── telemetry.py                  # mock provider (canned data)
│   └── source.py                     # mock provider (canned data)
├── fixtures/
│   ├── good_case.json                # all three providers return data
│   ├── missing_stack.json            # trend present, stack empty
│   └── bad_prompt_baseline.txt       # canned prompt-planned transcript
└── runner.py                         # DAG executor + transcript validator
```

The LLM never imports or calls a tool. Only `runner.py` does, and only at
`kind: tool` nodes in the order the DAG specifies.

## Running

```bash
# Good case: trend, stack, source, diagnosis, claim all written.
python runner.py run good_case.json
cat out/claim.json

# Missing-stack case: search_source is skipped via `requires`;
# the gate emits `blocked_missing` rather than crashing.
python runner.py run missing_stack.json
cat out/claim.json

# Baseline: replay a prompt-planned transcript against the validator.
# It called source.search_symbol before telemetry.get_stack_sample —
# the validator reports the inversion.
python runner.py validate fixtures/bad_prompt_baseline.txt
```

## What each fixture proves

| Fixture | Expected `out/claim.json` | `out/control_flow_report.json` |
|---|---|---|
| `good_case.json` | `verdict: supported` | `{ "errors": 0 }` |
| `missing_stack.json` | `verdict: blocked_missing` | `{ "errors": 0 }` |
| `bad_prompt_baseline.txt` | (no claim — validator only) | `{ "errors": >=1, "first": "source_search_before_stack" }` |

## Acceptance criteria

| ID | Criterion | Enforced by |
|---|---|---|
| A1 | Refuse to run a node before its `depends_on` artifacts exist on disk | `check_depends` in `runner.py` |
| A2 | `source.search_symbol` never runs without `stack_packet.top_frame` | `check_requires` + `requires:` in the DAG |
| A3 | Missing stack → `claim.verdict = blocked_missing`, not a crash | `run_gate_claim` |
| A4 | The LLM cannot call tools directly | only `runner.py` imports the tool modules; `diagnose` reads finished artifacts |
| A5 | Baseline transcript surfaces a named control-flow error | `validate_transcript` |
| A6 | This README states the demo addresses control-flow hallucination only | the second paragraph |

## What's deliberately out of scope

- The label inside `diagnosis` can be wrong (reasoning hallucination).
- Real-tool failure modes (timeouts, partial responses, auth).
- Multi-cycle state, transitions, notification dedup, fix proposals.

The point is reproducible by construction: we don't need probabilistic LLM
failures to demonstrate that prompt-planned execution **can** pick
`search_source` before `get_stack`. The validator scores the canned
transcript against the DAG and reports the inversion every time.
