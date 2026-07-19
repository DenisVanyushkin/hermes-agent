"""Offline calibration + synthetic-control runner (Step 4B).

Inputs: gold (Step 2 fixtures), provider output, contract. Outputs: per-fact
metrics — NO aggregate score. Read-only; artifact files only.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.model import VacancyUnderstanding
from job_intel.vacancy_understanding.semantic.contract import (
    ExtractionClass,
    load_semantic_contract,
)
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic
from job_intel.vacancy_understanding.semantic.runtime.provider import DeterministicPhraseProvider

FIXED_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)
# Controls that cannot be executed mechanically (no quoted phrase input or
# free-text semantics); each exemption carries the reason.
CONTROL_EXEMPTIONS = {
    "mandate.mandate_summary": "free-text synthesis, validated via golden replay not phrase controls",
    "risks.title_scope_mismatch": "requires title+body pair; covered by conflict-engine tests",
}


def _quoted(s: str) -> list[str]:
    return re.findall(r"'([^']+)'", s)


def _expected_value(s: str) -> str | None:
    m = re.search(r"->\s*\[?([a-z_|]+)", s)
    return m.group(1) if m else None


def _fact_value(fragment: dict, fact_id: str):
    sect, leaf = fact_id.split(".", 1)
    section = fragment.get(sect, {})
    if not isinstance(section, dict):  # risks.* live in a list section
        return None, None
    node = section.get(leaf)
    if isinstance(node, dict):
        v = node.get("value")
        return v, node.get("confidence")
    return None, None


def _base_vu(key: str, title: str, text: str) -> VacancyUnderstanding:
    return det_extract(RawVacancy(
        vacancy_key=key, source_system="synthetic_control", company="ControlCo",
        title=title, location="Remote", description=text), created_at=FIXED_TS)


def run_synthetic_controls(contract=None, provider=None) -> dict:
    contract = contract or load_semantic_contract()
    provider = provider or DeterministicPhraseProvider()
    results, failures = [], []
    for fact in contract.facts:
        if fact.extraction_class not in (ExtractionClass.semantic_only, ExtractionClass.hybrid):
            continue
        if fact.id in CONTROL_EXEMPTIONS or fact.controls is None:
            continue
        pos_value = _expected_value(fact.controls.positive)
        for kind in ("positive", "negative", "ambiguous", "unknown", "conflicting"):
            control = getattr(fact.controls, kind)
            phrases = _quoted(control)
            if not phrases or (kind == "conflicting" and len(phrases) < 2):
                results.append({"fact": fact.id, "kind": kind,
                                "status": "exempt_no_phrase",
                                "note": "control not mechanically constructible"})
                continue
            title = phrases[0] if "title" in control.lower() else "Synthetic Control Role"
            text = " … ".join(phrases)
            vu = _base_vu(f"control:{fact.id}:{kind}", title, text)
            out = extract_semantic(vu, title=title, text=text, provider=provider,
                                   contract=contract)
            value, conf = _fact_value(out.fragment, fact.id)
            raw = value if not isinstance(value, list) else "|".join(sorted(value))
            ok, note = _judge(kind, raw, conf, pos_value, control, out)
            row = {"fact": fact.id, "kind": kind, "status": "pass" if ok else "fail",
                   "value": raw, "confidence": conf, "note": note}
            results.append(row)
            if not ok:
                failures.append(row)
    return {"results": results, "failures": failures,
            "exemptions": CONTROL_EXEMPTIONS,
            "pass": sum(1 for r in results if r["status"] == "pass"),
            "fail": len(failures),
            "exempt": sum(1 for r in results if r["status"].startswith("exempt"))}


def _judge(kind, value, conf, pos_value, control, out):
    unknownish = value in (None, "unknown") or value == "" or value == "unknown"
    expected = _expected_value(control)
    has_contradiction = any(
        c.rule_id in ("cf_contradictory_observations", "cf_impossible_combination")
        for c in out.conflicts)
    if kind == "positive":
        if expected and expected not in ("risk",):
            return (str(value) == expected or (expected in str(value)),
                    f"expected {expected}")
        return (not unknownish, "expected any resolved value")
    if kind == "negative":
        return (str(value) != pos_value, f"must differ from positive {pos_value}")
    if kind == "ambiguous":
        return (unknownish or conf == "low", "unknown or low confidence")
    if kind == "unknown":
        if "low-confidence" in control:
            return (unknownish or conf == "low",
                    "unknown, or the low-confidence title value the control explicitly permits")
        return (unknownish, "must stay unknown")
    if kind == "conflicting":
        return (unknownish or has_contradiction or str(value) != pos_value,
                "unknown, contradiction risk, or non-positive value")
    return False, "unreachable"


def run_calibration(fixture_dir: Path, out_path: Path | None = None,
                    provider=None) -> dict:
    """Per-fact metrics of the provider vs Step 2 gold fixtures."""
    provider = provider or DeterministicPhraseProvider()
    contract = load_semantic_contract()
    semantic_ids = {f.id for f in contract.facts
                    if f.extraction_class in (ExtractionClass.semantic_only,
                                              ExtractionClass.hybrid)}
    per_fact = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0, "match": 0,
                                    "gold_known": 0, "emitted": 0,
                                    "unknown_emitted": 0, "conf": defaultdict(int)})
    clarif = 0
    cases = 0
    for f in sorted(fixture_dir.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        gold_doc = d["vacancy_understanding"]
        replay = d["replay_input"]
        title, text = replay["title"], replay.get("description") or ""
        base = det_extract(RawVacancy(**replay), created_at=FIXED_TS)
        out = extract_semantic(base, title=title, text=text, provider=provider)
        cases += 1
        clarif += len(out.clarifications)
        for fid in sorted(semantic_ids):
            g, _ = _fact_value(gold_doc, fid)
            e, ec = _fact_value(out.fragment, fid)
            g = None if g in (None, "unknown") or g == ["unknown"] else g
            e = None if e in (None, "unknown") or e == ["unknown"] else e
            m = per_fact[fid]
            if g is not None:
                m["gold_known"] += 1
            if e is not None:
                m["emitted"] += 1
                m["conf"][ec] += 1
            else:
                m["unknown_emitted"] += 1
            if g is not None and e is not None:
                m["tp"] += 1
                if str(e) == str(g) or (isinstance(g, list) and isinstance(e, list)
                                        and set(e) & set(g)):
                    m["match"] += 1
            elif g is not None:
                m["fn"] += 1
            elif e is not None:
                m["fp"] += 1
    report = {"cases": cases, "clarification_rate": round(clarif / cases, 2) if cases else None,
              "per_fact": {}}
    for fid, m in sorted(per_fact.items()):
        prec = round(m["match"] / m["emitted"], 3) if m["emitted"] else None
        rec = round(m["match"] / m["gold_known"], 3) if m["gold_known"] else None
        report["per_fact"][fid] = {
            "precision_vs_gold": prec, "recall_vs_gold": rec,
            "gold_known": m["gold_known"], "emitted": m["emitted"],
            "value_matches": m["match"], "false_positive_vs_gold": m["fp"],
            "unknown_rate": round(m["unknown_emitted"] / cases, 3) if cases else None,
            "confidence_distribution": dict(m["conf"])}
    if out_path:
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    import sys
    root = Path(".")
    controls = run_synthetic_controls()
    print(json.dumps({k: controls[k] for k in ("pass", "fail", "exempt")}, indent=1))
    for f in controls["failures"]:
        print("FAIL", f)
    rep = run_calibration(root / "tests/fixtures/vacancy_understanding",
                          Path("artifacts/shadow-evaluator/semantic-calibration.json"))
    print("calibration cases:", rep["cases"])
