# exception-spike-skill

Investigate an exception spike. The skill is compiled into a fixed runtime DAG
(`references/runtime_dag.yaml`); the LLM only reasons inside the `diagnose`
node and never selects tools or stage order.

## Stages

1. `get_trend` — fetch the exception trend over a window.
2. `get_stack` — fetch a representative stack for the spiking exception type.
3. `search_source` — look up the top frame symbol in source. Skipped when
   `stack_packet.top_frame` is missing.
4. `diagnose` (LLM) — summarize the three packets and propose a label.
5. `gate_claim` — deterministic: emit a claim iff all three packets exist.

The LLM cannot reorder stages, add edges, skip a node, or call a tool. The
runner owns the control plane; the model owns prose inside `diagnose`.
