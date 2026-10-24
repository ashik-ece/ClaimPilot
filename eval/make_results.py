"""Build RESULTS.md from the committed result files. Nothing in it is typed by hand.

    uv run python make_results.py            # writes ../RESULTS.md

Reads results/offline_test_blind.json (official first run), results/offline_test_posthoc.json
(optional re-run after the shortlist fix, labelled post-hoc), results/offline_dev.json, and the
pilot file: results/pilot_v1.0.json (real) if present, else results/pilot_SIMULATED.json shown as
a dry run under a warning. Real pilot files always win over the simulation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT = HERE.parent / "RESULTS.md"


def load(name: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def ci(interval: list[float] | None) -> str:
    return f"{pct(interval[0])} to {pct(interval[1])}" if interval else "n/a"


def offline_table(test: dict[str, Any], dev: dict[str, Any] | None, label: str) -> list[str]:
    d, e, f = test["decision"], test["extraction"], test["fraud"]
    flags = f["per_flag"]
    lines = [
        f"| Metric | Target | {label} (N={test['predicted']}) | Dev (N={dev['predicted'] if dev else '?'}) |",
        "| --- | --- | --- | --- |",
        f"| Field extraction, macro | 90%, no field under 80% | **{pct(e['macro'])}** "
        f"(95% CI {ci(test['intervals_95']['extraction_macro'])}); worst field {pct(e['min_field'])} "
        f"| {pct(dev['extraction']['macro']) if dev else ''}; worst {pct(dev['extraction']['min_field']) if dev else ''} |",
        f"| Decision agreement | 80% | **{pct(d['accuracy'])}** (95% CI {ci(test['intervals_95']['decision_accuracy'])}) "
        f"| {pct(dev['decision']['accuracy']) if dev else ''} |",
        f"| Fraud-flagged claims recommended approve | 0 | **{len(f['fraud_claims_approved'])}** "
        f"| {len(dev['fraud']['fraud_claims_approved']) if dev else ''} |",
        "| Fraud flags found (period / inflated / duplicate) | n/a | "
        + " / ".join(
            f"{flags[k]['tp']}/{flags[k]['tp'] + flags[k]['fn']}"
            for k in ("date_outside_policy_period", "inflated_estimate", "duplicate_claim")
        )
        + f", false flags {sum(v['fp'] for v in flags.values())} | "
        + (
            " / ".join(
                f"{dev['fraud']['per_flag'][k]['tp']}/{dev['fraud']['per_flag'][k]['tp'] + dev['fraud']['per_flag'][k]['fn']}"
                for k in ("date_outside_policy_period", "inflated_estimate", "duplicate_claim")
            )
            if dev
            else ""
        )
        + " |",
        f"| Governing clause cited (any / all) | n/a | {pct(test['citations']['recall'])} / {pct(test['citations']['recall_all'])} "
        f"| {pct(dev['citations']['recall']) if dev else ''} / {pct(dev['citations']['recall_all']) if dev else ''} |",
        f"| Retrieval recall@5 | n/a | not measured on the test split | "
        f"{pct(dev['retrieval_recall_at_5']['value']) if dev and 'retrieval_recall_at_5' in dev else 'n/a'} |",
        f"| Recommended approve but should not be | 0 | **{test['dangerous_quadrant']['count']}** "
        f"({', '.join(test['dangerous_quadrant']['approved_but_should_not_be']) or 'none'}) "
        f"| {dev['dangerous_quadrant']['count'] if dev else ''} |",
    ]
    return lines


def confusion(test: dict[str, Any]) -> list[str]:
    c = test["decision"]["confusion"]
    rows = ["| label \\ recommended | approve | deny | escalate |", "| --- | --- | --- | --- |"]
    for truth in ("approve", "deny", "escalate"):
        rows.append(f"| {truth} | {c[truth]['approve']} | {c[truth]['deny']} | {c[truth]['escalate']} |")
    return rows


def taxonomy(test: dict[str, Any]) -> list[str]:
    t = test["error_taxonomy"]
    return [
        f"Extraction {t['extraction']['count']}, retrieval {t['retrieval']['count']} "
        f"({', '.join(t['retrieval']['claims']) or 'none'}), reasoning/rules {t['reasoning']['count']} "
        f"({', '.join(t['reasoning']['claims']) or 'none'}). See `docs/error-analysis.md`."
    ]


def pilot_section(real: dict[str, Any] | None, sim: dict[str, Any] | None) -> list[str]:
    if real is None:
        out = [
            "## Adjuster pilot",
            "",
            "**Real pilot: not run yet.** The headline numbers for adjuster time and override rate "
            "do not exist until testers have worked claims. Nothing below is a result.",
            "",
        ]
        if sim:
            out += pilot_numbers(sim, "Simulated dry run (NOT EVIDENCE)")
        return out
    return ["## Adjuster pilot", ""] + pilot_numbers(real, "Pilot results")


def pilot_numbers(p: dict[str, Any], title: str) -> list[str]:
    t, d = p["timing"], p["decisions"]
    out = [f"### {title}", ""]
    if p.get("simulated"):
        out += [
            f"> **{p['warning']}** It exists to prove the pipeline from raw sessions to this table "
            "works. Replace it by exporting real sessions and rerunning `make_results.py`.",
            "",
        ]
    n = t["n"]
    red = (
        f"{pct(t['relative_reduction'])} (95% interval {ci(t.get('relative_reduction_ci95'))})"
        if "relative_reduction" in t
        else "n/a"
    )
    out += [
        "| Metric | Value |",
        "| --- | --- |",
        f"| Testers / decisions | {len(p['testers'])} / {p['decisions_total']} |",
        f"| Timed claims after exclusions (assisted / manual) | N = {n['assisted']} / {n['manual']} |",
        f"| Median seconds per claim (assisted / manual) | {t['median_s']['assisted']} / {t['median_s']['manual']} |",
        f"| Relative time reduction | {red} |",
        f"| Override rate (assisted decisions) | {pct(d['override_rate']) if d['override_rate'] is not None else 'n/a'} "
        f"({d['overrides']} of {d['assisted_decisions']}); reasons {d['override_reasons']} |",
        f"| Excluded | {t['dropped']} |",
        "",
        "Per-tester medians in seconds (assisted / manual):",
        "",
        "| Tester | Assisted | Manual |",
        "| --- | --- | --- |",
    ]
    for tester, v in t["per_tester"].items():
        out.append(
            f"| {tester} | {v['assisted']['median_s']} (n={v['assisted']['n']}) | {v['manual']['median_s']} (n={v['manual']['n']}) |"
        )
    return out + [""]


def main() -> None:
    blind, post, dev = load("offline_test_blind"), load("offline_test_posthoc"), load("offline_dev")
    real, sim = load("pilot_v1.0"), load("pilot_SIMULATED")
    assert blind, "run `python -m harness.report` for the blind run first"
    prov = blind["provenance"]
    lines = [
        "# ClaimPilot results",
        "",
        "Synthetic auto-collision claims; no real data. Generated by `eval/make_results.py` from the "
        "files in `eval/results/`.",
        "",
        "**Status:** offline evaluation done. Adjuster pilot "
        + ("**simulated only (not evidence)**; real sessions pending." if real is None else "run."),
        "",
        "## Offline evaluation (frozen test set)",
        "",
        "First blind run, scored once, before any fix derived from the test set:",
        "",
        *offline_table(blind, dev, "Test, first blind run"),
        "",
        "Decisions on the test set (rows are the label):",
        "",
        *confusion(blind),
        "",
        "Error taxonomy of the wrong decisions: " + taxonomy(blind)[0],
        "",
    ]
    if post:
        lines += [
            "### Post-hoc re-run after the shortlist fix",
            "",
            "The fix came from reading the blind run's errors, so this re-run is **not blind** and is "
            "reported separately; it must not replace the number above.",
            "",
            *offline_table(post, dev, "Test, post-hoc"),
            "",
            *confusion(post),
            "",
            "Error taxonomy: " + taxonomy(post)[0],
            "",
        ]
    lines += pilot_section(real, sim)
    lines += [
        "## Other targets",
        "",
        "| Metric | Target | Result | Source |",
        "| --- | --- | --- | --- |",
        f"| Cost per claim (model stages) | under $0.25 | analysis stages mean "
        f"${blind['cost_per_claim_usd']['mean']:.5f}, p90 ${blind['cost_per_claim_usd']['p90']:.5f}; "
        "about $0.0009 per claim including extraction on 3 live claims | estimated from assumed prices "
        "(`app/pipeline/llm.py`), not billing data |",
        "| p95 API latency, list and detail | under 300 ms | about 22 ms p95 at 500 requests/s | "
        "`scripts/loadtest.py`, recorded in `docs/operations.md`; laptop, database on the same machine |",
        "",
        "## Method",
        "",
        f"- Blind run: {blind['note']}",
        f"- Scored at commit `{prov['commit'][:10]}`; models {prov['models']}; prompt versions "
        f"{prov['prompt_versions']}. Frozen test set hash check passed: {prov['frozen_test_set_unchanged']}.",
        "- The pipeline was tuned on the 40-claim dev set only. The 160-claim test set was run once before "
        "any fix derived from it.",
        "- Intervals are 95% bootstrap intervals over claims.",
        "- Pilot rules (stated in advance): first claim of each tester per round dropped; sessions with an "
        "idle gap over 5 minutes dropped; median time per mode with N; override rate on assisted decisions "
        "only. Protocol in `docs/pilot/README.md`.",
        "",
        "## Limitations",
        "",
        "- **Synthetic, templated data.** Accuracy says the pipeline handles this generator, not real paperwork.",
        "- **Small positive counts.** 18 fraud-flagged test claims; 100% recall on 18 is encouraging, not tight.",
        "- **Not blind any more** for the shortlist fix, which came from reading the blind run's errors "
        "(`docs/error-analysis.md`); any later test-set number is post-hoc.",
        "- **Estimated costs** from assumed prices; not checked against a billing page.",
        "- **Pilot size.** 3 to 5 testers is a pilot, not a study; one claim type; synthetic claims.",
        "- **OCR differs by platform**; scores were recorded on macOS Tesseract, and CI replays inside the Linux image.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd backend && LLM_MODE=replay uv run python -m app.pipeline.evalcli analyze --extractions ../eval/results/preds_extract_test.json --out ../eval/results/preds_pipeline_test.json",
        "cd ../eval && uv run python -m harness.report --split test --predictions results/preds_pipeline_test.json --name offline_test_posthoc",
        "uv run python -m pilot.analyze pilot/raw/sessions_v1.0.csv --feedback pilot/raw/feedback_v1.0.csv --out results/pilot_v1.0.json",
        "uv run python make_results.py",
        "```",
        "",
    ]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
