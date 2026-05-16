from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .config import DEFAULT_CONFIG, load_config_bundle
from .dedup import canonical_vacancy_key, description_similarity, is_duplicate
from .digest import format_daily_digest, format_enrichment_questions
from .evaluator import score_vacancy, tier_for_score
from .enrichment import detect_high_value_questions
from .models import Vacancy
from .sources import discovery_queries, fetch_headhunter_vacancies, search_duckduckgo
from .store import JobIntelStore

DEFAULT_DB = Path.home() / ".hermes" / "job_intel" / "job_intel.sqlite3"


def _store() -> JobIntelStore:
    return JobIntelStore(DEFAULT_DB)


def _collect_vacancies() -> list[Vacancy]:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    queries = [
        "VP Product monetization B2C platform",
        "Head of Product fintech telecom",
        "Director of Product ecosystem growth",
    ]
    results: list[Vacancy] = []
    for query in queries:
        try:
            results.extend(fetch_headhunter_vacancies(query, per_page=10))
        except Exception:
            pass
    for _, query in discovery_queries():
        try:
            for hit in search_duckduckgo(query, max_results=5):
                results.append(
                    Vacancy(
                        source=hit.source,
                        source_id=hashlib.sha256(hit.url.encode("utf-8")).hexdigest()[:16],
                        company=_guess_company(hit.title),
                        title=hit.title,
                        location="Unknown",
                        url=hit.url,
                        description=hit.snippet or hit.title,
                    )
                )
        except Exception:
            continue
    return results


def _guess_company(title: str) -> str:
    parts = title.split("|")
    return parts[-1].strip() if parts else title[:80]


def run_daily() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("daily")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    dedup_cfg = cfg["deduplication"]
    similarity_threshold = dedup_cfg["secondary_similarity"]["description_similarity_threshold"]
    repost_window_days = dedup_cfg["repost_detection"]["repost_window_days"]

    vacancies = _collect_vacancies()
    accepted: list[tuple[Vacancy, object]] = []
    canonical_rows: list[Vacancy] = []

    for vacancy in vacancies:
        vacancy_key = canonical_vacancy_key(vacancy)
        is_dup = False
        dup_reason = None
        for existing in canonical_rows:
            if is_duplicate(vacancy, existing, similarity_threshold=similarity_threshold, repost_window_days=repost_window_days):
                is_dup = True
                dup_reason = canonical_vacancy_key(existing)
                store.save_duplicate(canonical_vacancy_key(existing), vacancy_key, "semantic/repost match", description_similarity(vacancy.description, existing.description))
                break
        store.upsert_vacancy(vacancy, vacancy_key)
        evaluation = score_vacancy(vacancy)
        store.save_evaluation(vacancy_key, evaluation, run_id=run_id)
        if evaluation.recommendation != "reject" and not is_dup:
            accepted.append((vacancy, evaluation))
            canonical_rows.append(vacancy)

    accepted.sort(key=lambda item: item[1].score, reverse=True)
    digest_items = accepted[: cfg["runtime"]["slack"]["batch_size"]]
    digest = format_daily_digest(digest_items)
    if digest != "[SILENT]":
        store.log_notification(run_id, cfg["runtime"]["slack"]["channel"], "daily_digest", digest)
    store.finish_run(run_id, status="ok", notes=f"found={len(vacancies)} accepted={len(accepted)}")
    return digest


def run_enrichment() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("enrichment")
    memory = store.get_memory()
    questions = detect_high_value_questions(memory)
    digest = format_enrichment_questions(questions)
    if digest != "[SILENT]":
        store.log_notification(run_id, "C0B42K4H4KV", "enrichment_questions", digest)
    store.finish_run(run_id, status="ok", notes=f"questions={len(questions)}")
    return digest


def run_alert_scan() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("alert")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    vacancies = _collect_vacancies()
    exceptional: list[tuple[Vacancy, object]] = []
    for vacancy in vacancies:
        evaluation = score_vacancy(vacancy)
        if evaluation.tier == "exceptional_fit":
            exceptional.append((vacancy, evaluation))
    exceptional.sort(key=lambda item: item[1].score, reverse=True)
    digest = format_daily_digest(exceptional[:3], title="Exceptional executive job alert")
    if digest != "[SILENT]":
        store.log_notification(run_id, cfg["runtime"]["slack"]["alerts_channel"], "alert", digest)
    store.finish_run(run_id, status="ok", notes=f"exceptional={len(exceptional)}")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-intel")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("daily")
    sub.add_parser("alert")
    sub.add_parser("enrichment")
    args = parser.parse_args(argv)

    store = _store()
    if args.cmd == "bootstrap":
        store.bootstrap()
        print(f"bootstrapped {DEFAULT_DB}")
        return 0
    if args.cmd == "daily":
        print(run_daily())
        return 0
    if args.cmd == "alert":
        print(run_alert_scan())
        return 0
    if args.cmd == "enrichment":
        print(run_enrichment())
        return 0
    return 1
