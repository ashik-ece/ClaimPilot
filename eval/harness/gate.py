"""Fail the build when a scored run falls below the committed thresholds.

    uv run python -m harness.gate results/pipeline_dev_ci.json thresholds.json

Exit code 0 when every check passes, 1 when any fails or a metric is missing (a metric that
cannot be found counts as a failure, so a broken run cannot pass by omission).
"""

from __future__ import annotations

import argparse
import json
import operator
import sys
from pathlib import Path
from typing import Any

OPS: dict[str, Any] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "len<=": lambda value, limit: len(value) <= limit,
}


def lookup(report: dict[str, Any], path: str) -> Any:
    node: Any = report
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def evaluate(report: dict[str, Any], thresholds: dict[str, Any]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for check in thresholds["checks"]:
        path, op, limit = check["path"], check["op"], check["value"]
        try:
            value = lookup(report, path)
            shown = len(value) if op.startswith("len") else value
            results.append((f"{path} {op} {limit}", bool(OPS[op](value, limit)), f"got {shown}"))
        except KeyError:
            results.append((f"{path} {op} {limit}", False, "metric missing from the report"))
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("thresholds", type=Path)
    args = ap.parse_args()
    results = evaluate(json.loads(args.report.read_text()), json.loads(args.thresholds.read_text()))
    for name, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name} ({detail})")
    failed = [name for name, ok, _ in results if not ok]
    print(f"evaluation gate FAILED: {len(failed)} check(s)" if failed else "evaluation gate passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
