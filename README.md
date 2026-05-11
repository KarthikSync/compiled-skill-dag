# compiled-skill-dag

A five-minute demo of one narrow claim:

> **Prompt-planned agents can choose invalid control flow; a compiled DAG
> makes that class of error impossible by construction.**

This demo addresses **control-flow hallucination only**, not reasoning
hallucination. The LLM inside `diagnose` can still produce a wrong label —
that is out of scope. What the DAG eliminates is the model's freedom to
reorder stages, skip dependencies, or call a tool it shouldn't.

Stdlib only — no `pip install` needed.

## Layout

```
.
├── skill/
│   ├── SKILL.md
│   └── references/runtime_dag.json   # the five-node graph
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

## Artifact contract

Every artifact written to `out/` carries an explicit `status`:

| `status` | meaning |
|---|---|
| `ok` | the node ran and returned data |
| `missing` | the node ran but the provider returned nothing |
| `skipped` | the node did not run (its `requires` was unsatisfied) |

The deterministic `gate_claim` node uses status (not file existence) to
decide between `supported` and `blocked_missing`. Its rule is named in the
DAG (`gate: all_required_packets_ok`) and looked up in a dispatch table —
not free text.

## Running

```bash
# Run everything and assert verdicts (recommended).
python runner.py test

# Or run the three commands individually:
python runner.py run good_case.json
python runner.py run missing_stack.json
python runner.py validate fixtures/bad_prompt_baseline.txt
```

`run` and `validate` both clear `out/*.json` first, so a stale `claim.json`
from an earlier run never leaks into a later one.

## Expected output

### `python runner.py test`

```
ran   get_trend
ran   get_stack
ran   search_source
ran   diagnose
ran   gate_claim
PASS  good_case.json: expected='supported' got='supported'
ran   get_trend
ran   get_stack
skip  search_source: missing required 'stack_packet.top_frame'
ran   diagnose
ran   gate_claim
PASS  missing_stack.json: expected='blocked_missing' got='blocked_missing'
PASS  bad_prompt_baseline.txt: first='source_search_before_stack' errors=2
```

### `out/claim.json` — good case

```json
{
  "verdict": "supported",
  "label": "likely_null_deref",
  "summary": "NullReferenceException spiked 17.75x (142 in 1h).",
  "top_frame": "OrderService.HandleRequest"
}
```

### `out/claim.json` — missing-stack case

```json
{
  "verdict": "blocked_missing",
  "missing": ["source_packet", "stack_packet"],
  "details": {
    "stack_packet": "missing",
    "source_packet": "skipped"
  }
}
```

### Baseline transcript

```json
{
  "errors": 2,
  "first": "source_search_before_stack",
  "all": ["source_search_before_stack", "stack_before_trend"]
}
```

## Acceptance criteria

| ID | Criterion | Enforced by |
|---|---|---|
| A1 | Refuse to run a node before its `depends_on` artifacts exist on disk | `check_depends` in `runner.py` |
| A2 | `source.search_symbol` never runs without `stack_packet.top_frame` | `check_requires` + `requires:` in the DAG |
| A3 | Missing stack → `claim.verdict = blocked_missing`, not a crash | `gate_all_required_packets_ok` |
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
