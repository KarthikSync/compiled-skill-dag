#!/usr/bin/env python3
"""Minimal runtime DAG executor.

Subcommands:
  run <fixture.json>         execute the DAG against a fixture
  validate <transcript.txt>  score a prompt-planned transcript against the DAG
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from tools import source, telemetry

ROOT = Path(__file__).parent
DAG_PATH = ROOT / "skill" / "references" / "runtime_dag.yaml"
OUT_DIR = ROOT / "out"

TOOL_REGISTRY = {
    "telemetry.get_exception_trend": telemetry.get_exception_trend,
    "telemetry.get_stack_sample": telemetry.get_stack_sample,
    "source.search_symbol": source.search_symbol,
}

SHORT_ID = {"get_trend": "trend", "get_stack": "stack", "search_source": "source_search"}


def artifact_path(name):
    return OUT_DIR / f"{name}.json"


def load_artifact(name):
    p = artifact_path(name)
    return json.loads(p.read_text()) if p.exists() else None


def write_artifact(name, value):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path(name).write_text(json.dumps(value, indent=2) + "\n")


def resolve_dotted(path):
    parts = path.split(".")
    cur = load_artifact(parts[0])
    for p in parts[1:]:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


DAG = yaml.safe_load(DAG_PATH.read_text())
NODES_BY_ID = {n["id"]: n for n in DAG["nodes"]}


def tool_args_for(tool_name, fixture):
    if tool_name == "telemetry.get_exception_trend":
        return {"fixture": fixture, "window": "1h"}
    if tool_name == "telemetry.get_stack_sample":
        trend = load_artifact("trend_packet") or {}
        return {"fixture": fixture, "exception_type": trend.get("exception_type")}
    if tool_name == "source.search_symbol":
        return {"fixture": fixture, "symbol": resolve_dotted("stack_packet.top_frame")}
    raise KeyError(tool_name)


def check_depends(node, skipped):
    for dep_id in node.get("depends_on", []):
        if dep_id in skipped:
            continue
        for out in NODES_BY_ID[dep_id].get("outputs", []):
            if not artifact_path(out).exists():
                return False, f"missing artifact '{out}' from '{dep_id}'"
    return True, None


def check_requires(node):
    for req in node.get("requires", []):
        if resolve_dotted(req) in (None, "", [], {}):
            return False, req
    return True, None


def run_tool_node(node, fixture):
    fn = TOOL_REGISTRY[node["tool"]]
    result = fn(**tool_args_for(node["tool"], fixture))
    for out in node["outputs"]:
        write_artifact(out, result if result is not None else {})


def run_diagnose(_node, _fixture):
    # Stand-in for the LLM: a real implementation calls a model here.
    trend = load_artifact("trend_packet") or {}
    stack = load_artifact("stack_packet") or {}
    src = load_artifact("source_packet") or {}
    etype = (trend.get("exception_type") or "").lower()
    snippet = (src.get("snippet") or "").lower()
    if "null" in etype or ".find(" in snippet:
        label = "likely_null_deref"
    elif "timeout" in etype:
        label = "likely_throughput_regression"
    else:
        label = "unknown"
    write_artifact("diagnosis", {
        "label": label,
        "top_frame": stack.get("top_frame"),
        "summary": (
            f"{trend.get('exception_type', 'unknown')} spiked "
            f"{trend.get('spike_factor', '?')}x ({trend.get('count_1h', '?')} in 1h)."
        ),
    })


def run_gate_claim(_node, _fixture):
    packets = {n: load_artifact(n) for n in ("trend_packet", "stack_packet", "source_packet")}
    missing = [n for n, v in packets.items() if not v]
    if missing:
        write_artifact("claim", {"verdict": "blocked_missing", "missing": missing})
        return
    d = load_artifact("diagnosis") or {}
    write_artifact("claim", {
        "verdict": "supported",
        "label": d.get("label"),
        "summary": d.get("summary"),
        "top_frame": d.get("top_frame"),
    })


KIND_DISPATCH = {"tool": run_tool_node, "llm": run_diagnose, "deterministic": run_gate_claim}


def run_dag(fixture_name):
    if OUT_DIR.exists():
        for p in OUT_DIR.glob("*.json"):
            p.unlink()
    fixture = json.loads((ROOT / "fixtures" / fixture_name).read_text())
    skipped = set()
    for node in DAG["nodes"]:
        ok, why = check_depends(node, skipped)
        if not ok:
            print(f"refuse {node['id']}: {why}", file=sys.stderr)
            sys.exit(2)
        ok, why = check_requires(node)
        if not ok:
            skipped.add(node["id"])
            print(f"skip  {node['id']}: requires '{why}' is empty")
            continue
        KIND_DISPATCH[node["kind"]](node, fixture)
        print(f"ran   {node['id']}")
    write_artifact("control_flow_report", {"errors": 0})


TOOL_CALL_RE = re.compile(r">\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\(")


def validate_transcript(transcript_path):
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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("fixture")
    sub.add_parser("validate").add_argument("transcript")
    args = ap.parse_args()
    if args.cmd == "run":
        run_dag(args.fixture)
    else:
        print(json.dumps(validate_transcript(args.transcript), indent=2))


if __name__ == "__main__":
    main()
