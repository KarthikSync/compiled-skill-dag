#!/usr/bin/env python3
"""Minimal runtime DAG executor.

The graph in `skill/references/runtime_dag.json` owns stage order and the set
of allowed tool calls. The LLM is only invoked inside `diagnose`, and only
ever sees finished artifacts — it cannot call tools, reorder nodes, or skip
a stage.

Each artifact on disk carries an explicit `status`:

  * `ok`       — a tool ran and returned data
  * `missing`  — a tool ran and returned nothing
  * `skipped`  — the node did not run (its `requires` was unsatisfied)

The deterministic `gate_claim` node uses status, not file existence, to
decide whether to emit `verdict: supported` or `verdict: blocked_missing`.

Subcommands:
  run <fixture.json>         execute the DAG against a fixture
  validate <transcript.txt>  score a prompt-planned transcript against the DAG
  test                       run all three fixtures and assert verdicts
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from tools import source, telemetry

ROOT = Path(__file__).parent
DAG_PATH = ROOT / "skill" / "references" / "runtime_dag.json"
OUT_DIR = ROOT / "out"

TOOL_REGISTRY = {
    "telemetry.get_exception_trend": telemetry.get_exception_trend,
    "telemetry.get_stack_sample": telemetry.get_stack_sample,
    "source.search_symbol": source.search_symbol,
}

SHORT_ID = {"get_trend": "trend", "get_stack": "stack", "search_source": "source_search"}

DAG = json.loads(DAG_PATH.read_text())
NODES_BY_ID = {n["id"]: n for n in DAG["nodes"]}


def artifact_path(name):
    return OUT_DIR / f"{name}.json"


def load_artifact(name):
    p = artifact_path(name)
    return json.loads(p.read_text()) if p.exists() else None


def write_artifact(name, value):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path(name).write_text(json.dumps(value, indent=2) + "\n")


def clear_outputs():
    if OUT_DIR.exists():
        for p in OUT_DIR.glob("*.json"):
            p.unlink()


def resolve_dotted(path):
    parts = path.split(".")
    cur = load_artifact(parts[0])
    for p in parts[1:]:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def tool_args_for(tool_name, fixture):
    if tool_name == "telemetry.get_exception_trend":
        return {"fixture": fixture, "window": "1h"}
    if tool_name == "telemetry.get_stack_sample":
        trend = load_artifact("trend_packet") or {}
        return {"fixture": fixture, "exception_type": trend.get("exception_type")}
    if tool_name == "source.search_symbol":
        return {"fixture": fixture, "symbol": resolve_dotted("stack_packet.top_frame")}
    raise KeyError(tool_name)


def check_depends(node):
    """A1: refuse to run a node whose dependency outputs are missing on disk.

    Every node now writes its outputs (with explicit status) whether it ran,
    was skipped, or produced no data — so dependency artifacts always exist
    by the time a downstream node is reached.
    """
    for dep_id in node.get("depends_on", []):
        for out in NODES_BY_ID[dep_id].get("outputs", []):
            if not artifact_path(out).exists():
                return False, f"missing artifact '{out}' from '{dep_id}'"
    return True, None


def check_requires(node):
    for req in node.get("requires", []):
        if resolve_dotted(req) in (None, "", [], {}):
            return False, req
    return True, None


def write_skipped(node, reason):
    for out in node["outputs"]:
        write_artifact(out, {"status": "skipped", "reason": reason})


def run_tool_node(node, fixture):
    fn = TOOL_REGISTRY[node["tool"]]
    data = fn(**tool_args_for(node["tool"], fixture)) or {}
    for out in node["outputs"]:
        if data:
            write_artifact(out, {"status": "ok", **data})
        else:
            write_artifact(out, {
                "status": "missing",
                "reason": f"{node['tool']} returned no data",
            })


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
ALLOWED_LABELS = ("likely_null_deref", "likely_throughput_regression", "unknown")


def stub_diagnose(trend, stack, src):
    etype = (trend.get("exception_type") or "").lower()
    snippet = (src.get("snippet") or "").lower()
    if "null" in etype or ".find(" in snippet:
        label = "likely_null_deref"
    elif "timeout" in etype:
        label = "likely_throughput_regression"
    else:
        label = "unknown"
    summary = (
        f"{trend.get('exception_type', 'unknown')} spiked "
        f"{trend.get('spike_factor', '?')}x ({trend.get('count_1h', '?')} in 1h)."
    )
    return label, summary


def call_openrouter(prompt, api_key, model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    content = body["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group())


def llm_diagnose(trend, stack, src):
    prompt = (
        "You are diagnosing an exception spike. Three evidence packets:\n\n"
        f"trend_packet:  {json.dumps(trend)}\n"
        f"stack_packet:  {json.dumps(stack)}\n"
        f"source_packet: {json.dumps(src)}\n\n"
        "Return ONLY a JSON object with two keys:\n"
        f'  "label":   one of {list(ALLOWED_LABELS)}\n'
        '  "summary": one sentence describing what spiked and by how much.\n'
    )
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    result = call_openrouter(prompt, api_key, model)
    label = result.get("label", "unknown")
    if label not in ALLOWED_LABELS:
        label = "unknown"
    return label, str(result.get("summary", "")).strip()


def run_diagnose(_node, _fixture):
    trend = load_artifact("trend_packet") or {}
    stack = load_artifact("stack_packet") or {}
    src = load_artifact("source_packet") or {}
    source_kind = "stub"
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            label, summary = llm_diagnose(trend, stack, src)
            source_kind = "openrouter:" + os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
            print(f"warn  diagnose: LLM call failed ({type(e).__name__}); "
                  f"falling back to stub", file=sys.stderr)
            label, summary = stub_diagnose(trend, stack, src)
    else:
        print("warn  diagnose: OPENROUTER_API_KEY not set; using deterministic stub",
              file=sys.stderr)
        label, summary = stub_diagnose(trend, stack, src)
    write_artifact("diagnosis", {
        "status": "ok",
        "source": source_kind,
        "label": label,
        "top_frame": stack.get("top_frame"),
        "summary": summary,
    })


def gate_all_required_packets_ok(_node, _fixture):
    packets = {n: load_artifact(n) or {} for n in ("trend_packet", "stack_packet", "source_packet")}
    not_ok = {n: p.get("status", "absent") for n, p in packets.items() if p.get("status") != "ok"}
    if not_ok:
        write_artifact("claim", {
            "verdict": "blocked_missing",
            "missing": sorted(not_ok.keys()),
            "details": not_ok,
        })
        return
    d = load_artifact("diagnosis") or {}
    write_artifact("claim", {
        "verdict": "supported",
        "label": d.get("label"),
        "summary": d.get("summary"),
        "top_frame": d.get("top_frame"),
    })


GATE_DISPATCH = {"all_required_packets_ok": gate_all_required_packets_ok}


def run_deterministic(node, fixture):
    GATE_DISPATCH[node["gate"]](node, fixture)


KIND_DISPATCH = {"tool": run_tool_node, "llm": run_diagnose, "deterministic": run_deterministic}


def run_dag(fixture_name):
    clear_outputs()
    fixture = json.loads((ROOT / "fixtures" / fixture_name).read_text())
    for node in DAG["nodes"]:
        ok, why = check_depends(node)
        if not ok:
            print(f"refuse {node['id']}: {why}", file=sys.stderr)
            sys.exit(2)
        ok, req = check_requires(node)
        if not ok:
            reason = f"missing required '{req}'"
            write_skipped(node, reason)
            print(f"skip  {node['id']}: {reason}")
            continue
        KIND_DISPATCH[node["kind"]](node, fixture)
        print(f"ran   {node['id']}")
    write_artifact("control_flow_report", {"errors": 0})


TOOL_CALL_RE = re.compile(r">\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\(")


def validate_transcript(transcript_path):
    clear_outputs()
    text = Path(transcript_path).read_text()
    by_tool = {n["tool"]: n for n in DAG["nodes"] if n["kind"] == "tool"}
    tool_ids = {n["id"] for n in DAG["nodes"] if n["kind"] == "tool"}
    seen, errors = set(), []
    for call in TOOL_CALL_RE.findall(text):
        node = by_tool.get(call)
        if not node:
            continue
        for dep in node.get("depends_on", []):
            if dep in tool_ids and dep not in seen:
                errors.append(
                    f"{SHORT_ID.get(node['id'], node['id'])}"
                    f"_before_{SHORT_ID.get(dep, dep)}"
                )
        seen.add(node["id"])
    report = {"errors": len(errors), "first": errors[0] if errors else None, "all": errors}
    write_artifact("control_flow_report", report)
    return report


def cmd_test():
    cases = [("good_case.json", "supported"), ("missing_stack.json", "blocked_missing")]
    failures = 0
    for fixture, expected in cases:
        run_dag(fixture)
        actual = (load_artifact("claim") or {}).get("verdict")
        ok = actual == expected
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {fixture}: expected={expected!r} got={actual!r}")
    report = validate_transcript(str(ROOT / "fixtures" / "bad_prompt_baseline.txt"))
    ok = report["errors"] >= 1 and report["first"] == "source_search_before_stack"
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  bad_prompt_baseline.txt: "
          f"first={report['first']!r} errors={report['errors']}")
    sys.exit(0 if failures == 0 else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("fixture")
    sub.add_parser("validate").add_argument("transcript")
    sub.add_parser("test")
    args = ap.parse_args()
    if args.cmd == "run":
        run_dag(args.fixture)
    elif args.cmd == "validate":
        print(json.dumps(validate_transcript(args.transcript), indent=2))
    elif args.cmd == "test":
        cmd_test()


if __name__ == "__main__":
    main()
