#!/usr/bin/env python3
"""
Golden routing corpus generator.

Run manually when a deliberate routing change requires updating the golden corpus:

    cd /home/hermes/.hermes/hermes-agent
    python tests/fixtures/role_packages/generate_corpus.py

Writes: tests/fixtures/role_packages/golden_routing_corpus.yaml

DO NOT run automatically in CI. Every regeneration is a deliberate golden
approval that must be code-reviewed alongside the routing change that prompted it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / ".venv" / "lib" / "python3.11" / "site-packages"))

import yaml  # noqa: E402 — path setup must precede imports

from hermes_cli.profile_routing import (  # noqa: E402
    route_task,
    _SECURITY_TERMS,
    _INFRA_TERMS,
    _CAREER_TERMS,
    _DOCS_TERMS,
    _RESEARCH_TERMS,
)

_REGISTRY_PATH = _REPO_ROOT / "config" / "hermes-profiles.yaml"
_POLICY_PATH = _REPO_ROOT / "config" / "hermes-model-policy.yaml"
_OUTPUT_PATH = Path(__file__).parent / "golden_routing_corpus.yaml"

# ---------------------------------------------------------------------------
# Seed cases: (id, description, category, lang, prompt)
#
# Coverage goals:
#   - At least one EN and one RU carrier sentence per category that has RU terms.
#   - All five domain categories: security, infra, career, docs, research.
#   - Docs-first markers (should beat infra in primary selection).
#   - All four required overlays: engineer+security, engineer+scribe,
#     career+researcher, security+scribe.
#   - Note on max-chain-limit: current _build_overlays produces at most 2
#     overlays, making the route chain at most 3 hops — exactly the default
#     max_chain_limit of 3. Therefore max_chain_limit_applied is always False
#     under the current routing logic with default parameters.
#   - Benign fallback: EN, RU, and timer-like prompt.
#   - Context stripping: thread-context block, cronjob-response block,
#     replying-to line behaviour (passes through in routing_request_text).
# ---------------------------------------------------------------------------

SEED_CASES: list[tuple[str, str, str, str, str]] = [
    # ── Security (EN only — no RU terms in _SECURITY_TERMS) ─────────────────
    (
        "security_en_auth_tokens",
        "auth + tokens trigger security_auditor",
        "security", "en",
        "Please check the auth tokens for exposure in the production system",
    ),
    (
        "security_en_secrets_public_access",
        "secrets + public access trigger security_auditor",
        "security", "en",
        "There are secrets exposed via public access — review immediately",
    ),
    (
        "security_en_cloudflare_firewall_permissions",
        "cloudflare + firewall + permissions trigger security_auditor",
        "security", "en",
        "Review the cloudflare firewall permissions configuration",
    ),
    (
        "security_en_scheduler_tool_boundary",
        "scheduler + tool-boundary trigger security_auditor",
        "security", "en",
        "The scheduler has a tool-boundary privilege escalation risk",
    ),
    (
        "security_en_security_audit_threat_model",
        "security audit + threat model trigger security_auditor",
        "security", "en",
        "Perform a security audit and produce a threat model for the gateway",
    ),
    (
        "security_en_privileged_browser_profile",
        "privileged + browser profile trigger security_auditor",
        "security", "en",
        "Review the privileged browser profile — it may expose tokens",
    ),
    # ── Infra / Engineering (EN only — no RU terms in _INFRA_TERMS) ─────────
    (
        "infra_en_deploy_webui",
        "deploy + webui trigger engineer",
        "infra", "en",
        "Deploy the webui service to the production host",
    ),
    (
        "infra_en_docker_logs",
        "docker + logs trigger engineer",
        "infra", "en",
        "Check the docker container logs for errors",
    ),
    (
        "infra_en_systemd_reload",
        "systemd + reload trigger engineer",
        "infra", "en",
        "Reload the systemd service configuration after the patch",
    ),
    (
        "infra_en_database_rollback",
        "database + rollback trigger engineer",
        "infra", "en",
        "The database migration failed — prepare a rollback plan",
    ),
    (
        "infra_en_monitoring_regression",
        "monitoring + regression + investigate trigger engineer",
        "infra", "en",
        "Investigate the monitoring regression in the approval-gate service",
    ),
    (
        "infra_en_rebase_build",
        "rebase + build trigger engineer",
        "infra", "en",
        "Apply the rebase and rebuild the production service",
    ),
    # ── Career (EN + RU) ─────────────────────────────────────────────────────
    (
        "career_en_vacancy_apply",
        "vacancy + apply trigger career_strategist",
        "career", "en",
        "I found a vacancy for a Head of Product role — should I apply?",
    ),
    (
        "career_en_cv_cover_letter",
        "cv + cover letter trigger career_strategist",
        "career", "en",
        "Help me update my CV and cover letter for this application",
    ),
    (
        "career_en_job_intel_opportunity",
        "job-intel + job opportunity trigger career_strategist",
        "career", "en",
        "Review this job-intel report for the latest job opportunity",
    ),
    (
        "career_en_interview_strategy",
        "interview + application strategy trigger career_strategist",
        "career", "en",
        "Prepare me for the interview using an application strategy",
    ),
    (
        "career_ru_otsenka_vakansii",
        "Russian: оцени вакансию triggers career_strategist",
        "career", "ru",
        "Оцени вакансию Head of Product для меня",
    ),
    (
        "career_ru_stoit_li_otklikat",
        "Russian: стоит ли откликаться triggers career_strategist",
        "career", "ru",
        "Стоит ли откликаться на роль VP Product в этой компании?",
    ),
    (
        "career_ru_rezyume_soprov",
        "Russian: резюме + сопроводительное письмо trigger career_strategist",
        "career", "ru",
        "Подготовь резюме и сопроводительное письмо для вакансии CPO",
    ),
    (
        "career_ru_karyer_strategiya",
        "Russian: карьер triggers career_strategist",
        "career", "ru",
        "Помоги выстроить карьерную стратегию для роли CPO",
    ),
    # ── Docs / Scribe (EN + RU) ──────────────────────────────────────────────
    (
        "docs_en_runbook_state_decision",
        "runbook + state + decision trigger scribe",
        "docs", "en",
        "Create a runbook capturing the system state and key decisions",
    ),
    (
        "docs_en_profile_handoff",
        "profile handoff + record the decision trigger scribe",
        "docs", "en",
        "Write a profile handoff to record the decision we made today",
    ),
    (
        "docs_en_update_docs_state",
        "update docs + update state trigger scribe",
        "docs", "en",
        "Update docs and update state for this configuration change",
    ),
    (
        "docs_en_capture_durable_memory",
        "capture durable memory trigger scribe",
        "docs", "en",
        "Capture durable memory of today's work and capture the outcome",
    ),
    (
        "docs_ru_zafiksiruy_itog",
        "Russian: зафиксируй итог triggers scribe",
        "docs", "ru",
        "Зафиксируй итог сегодняшней работы по ролям",
    ),
    (
        "docs_ru_zapishi_sohrani",
        "Russian: запиши итог + сохрани в документацию trigger scribe",
        "docs", "ru",
        "Запиши итог и сохрани в документацию",
    ),
    (
        "docs_ru_handoff",
        "Russian: напиши handoff triggers scribe",
        "docs", "ru",
        "Напиши handoff для команды по итогам сессии",
    ),
    (
        "docs_ru_obnovit_docs",
        "Russian: обнови docs + обнови документацию trigger scribe",
        "docs", "ru",
        "Обнови docs и обнови документацию по последнему изменению",
    ),
    # ── Docs-first markers (beat infra/security in primary selection) ────────
    (
        "docs_first_en_final_status_over_infra",
        "final status (docs-first marker) beats docker (infra) for primary",
        "docs_first", "en",
        "Provide a final status of the docker deployment process",
    ),
    (
        "docs_first_en_status_update",
        "status update (docs-first marker) routes to scribe",
        "docs_first", "en",
        "Please provide a status update on the current project",
    ),
    (
        "docs_first_en_handoff_today",
        "handoff (docs-first marker) routes to scribe",
        "docs_first", "en",
        "Write a handoff for today's hermes role work",
    ),
    (
        "docs_first_en_update_state",
        "update state (docs-first marker) routes to scribe",
        "docs_first", "en",
        "Update state for the current system configuration",
    ),
    (
        "docs_first_ru_finalnyy_status",
        "Russian: финальный статус (docs-first marker) routes to scribe",
        "docs_first", "ru",
        "Зафиксируй финальный статус Hermes roles MVP",
    ),
    # ── Research (EN + RU) ───────────────────────────────────────────────────
    (
        "research_en_weather_forecast",
        "weather trigger researcher",
        "research", "en",
        "What is the weather forecast for today?",
    ),
    (
        "research_en_news_digest_market",
        "news + digest + market overview trigger researcher",
        "research", "en",
        "Give me a news digest and market overview for this week",
    ),
    (
        "research_en_btc_fees_coinbase",
        "bitcoin + coinbase + fees trigger researcher",
        "research", "en",
        "Compare bitcoin fees on binance and coinbase",
    ),
    (
        "research_en_due_diligence",
        "due diligence + company research trigger researcher",
        "research", "en",
        "Do due diligence and company research for the current context",
    ),
    (
        "research_ru_pogoda_prognoz",
        "Russian: погода + прогноз погоды trigger researcher",
        "research", "ru",
        "Какой прогноз погоды на сегодня в Алматы?",
    ),
    (
        "research_ru_kupit_btc_komissii",
        "Russian: купить btc + комиссии trigger researcher",
        "research", "ru",
        "Где лучше купить BTC? Сравни комиссии на разных биржах",
    ),
    # ── Overlays ─────────────────────────────────────────────────────────────
    (
        "overlay_engineer_security_scribe",
        "engineer primary + security + scribe overlays (3-hop chain)",
        "overlay", "en",
        "production WebUI exposure change",
    ),
    (
        "overlay_career_researcher",
        "career_strategist primary + researcher overlay",
        "overlay", "en",
        "job opportunity review requiring company research and a CV update",
    ),
    (
        "overlay_engineer_scribe",
        "engineer primary + scribe overlay from docs signal",
        "overlay", "en",
        "operational change with durable state impact",
    ),
    (
        "overlay_security_scribe",
        "security_auditor primary + scribe overlay from document signal",
        "overlay", "en",
        "Review the auth tokens and document the security findings",
    ),
    # max-chain-limit note: _build_overlays yields at most 2 overlays, making
    # the chain at most 3 hops (= the default max_chain_limit of 3).
    # max_chain_limit_applied is therefore always False with current routing.
    # This entry uses a prompt that produces the maximum 3-hop chain to verify
    # the limit boundary without triggering truncation.
    (
        "overlay_max_chain_boundary_3_hops",
        "maximum 3-hop chain — at the limit but not truncated",
        "overlay", "en",
        "production WebUI exposure change",  # same as above, max 3 hops
    ),
    # ── Benign fallbacks → general_operator ──────────────────────────────────
    (
        "benign_en_haircut",
        "personal errand with no domain triggers → general_operator",
        "benign", "en",
        "Book me a haircut appointment for tomorrow",
    ),
    (
        "benign_en_admin_task",
        "safe admin task with no domain triggers → general_operator",
        "benign", "en",
        "Help me with a simple safe admin task",
    ),
    (
        "benign_en_reminder",
        "timer/reminder prompt with no domain triggers → general_operator",
        "benign", "en",
        "Set a reminder for tomorrow at 9am",
    ),
    (
        "benign_ru_help",
        "Russian personal errand with no domain triggers → general_operator",
        "benign", "ru",
        "Помоги мне с личными делами",
    ),
    (
        "benign_ru_reminder",
        "Russian reminder prompt with no domain triggers → general_operator",
        "benign", "ru",
        "Напомни мне о встрече завтра утром",
    ),
    # ── Context stripping cases ───────────────────────────────────────────────
    (
        "strip_thread_context_hot_security_terms",
        "security hot terms inside thread context block are stripped; weather drives routing",
        "stripping", "en",
        (
            "[Thread context from Slack]\n"
            "[thread reply] auth secrets token exposure cloudflare firewall permissions\n"
            "[End of thread context]\n\n"
            "What is the weather forecast for today?"
        ),
    ),
    (
        "strip_cronjob_response_hot_infra_terms",
        "infra hot terms inside cronjob response block are stripped; weather drives routing",
        "stripping", "en",
        (
            "Cronjob response: hermes-rebase-local-customizations result\n"
            "docker systemd deploy database production monitoring logs\n\n"
            "What is the weather forecast for today?"
        ),
    ),
    (
        "strip_thread_context_replying_to_engineer_no_security_overlay",
        "security terms in thread body stripped; replying-to line with rebase keeps engineer primary; no security overlay",
        "stripping", "en",
        (
            "[Replying to: hermes-rebase-local-customizations]\n"
            "[Thread context from Slack thread]\n"
            "[thread reply] provider credentials changed, gateway deploy, auth json conflicts\n"
            "[End of thread context]\n\n"
            "отчет вызывает у меня двоякое ощущение. давай его полностью переделаем. сделай план и покажи мне"
        ),
    ),
    (
        "strip_replying_to_no_hot_terms_benign",
        "replying-to line passes through routing but contains no trigger terms; prompt is benign",
        "stripping", "en",
        "[Replying to: morning-greeting-note]\nHelp me plan my day",
    ),
]


def _entry_from_case(case_id: str, description: str, category: str, lang: str, prompt: str) -> dict:
    decision = route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)
    return {
        "id": case_id,
        "description": description,
        "category": category,
        "lang": lang,
        "prompt": prompt,
        "expected": {
            "primary_profile": decision.primary_profile,
            "route_chain": [hop.profile_id for hop in decision.route_chain],
            "confidence": decision.confidence,
            "ambiguity_reasons": list(decision.ambiguity_reasons),
            "max_chain_limit_applied": decision.max_chain_limit_applied,
        },
    }


def generate() -> None:
    entries = [_entry_from_case(*case) for case in SEED_CASES]
    entries.sort(key=lambda e: e["id"])

    corpus = {
        "version": "1",
        "schema_version": "1",
        "generated_by": "tests/fixtures/role_packages/generate_corpus.py",
        "note": (
            "DO NOT edit by hand. Corpus updates require explicit golden approval: "
            "run the generator, review the diff, commit alongside the routing change."
        ),
        "entries": entries,
    }

    _OUTPUT_PATH.write_text(
        yaml.dump(corpus, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} entries to {_OUTPUT_PATH}")

    # Print a summary so the operator can verify coverage at a glance.
    categories: dict[str, int] = {}
    for e in entries:
        categories[e["category"]] = categories.get(e["category"], 0) + 1
    profiles: dict[str, int] = {}
    for e in entries:
        pp = e["expected"]["primary_profile"]
        profiles[pp] = profiles.get(pp, 0) + 1
    print("\nCategory breakdown:")
    for cat, cnt in sorted(categories.items()):
        print(f"  {cat:30s} {cnt}")
    print("\nPrimary profile breakdown:")
    for pp, cnt in sorted(profiles.items()):
        print(f"  {pp:30s} {cnt}")


def check_parity() -> bool:
    """Compare routing terms from Python constants vs YAML triggers file.

    Returns True if they match, False (with printed diff) otherwise.
    """
    from hermes_cli.profile_routing import (
        get_builtin_routing_terms_from_constants,
        get_builtin_routing_terms_from_yaml,
        _ROUTING_DOMAINS,
    )

    _TRIGGERS_PATH = _REPO_ROOT / "config" / "hermes-routing-triggers.yaml"
    constants = get_builtin_routing_terms_from_constants()
    yaml_terms = get_builtin_routing_terms_from_yaml(_TRIGGERS_PATH)

    all_keys = list(_ROUTING_DOMAINS) + ["docs_first_markers"]
    mismatches: list[str] = []
    for key in all_keys:
        c_set = set(constants.get(key, []))
        y_set = set(yaml_terms.get(key, []))
        missing = c_set - y_set
        extra = y_set - c_set
        if missing:
            mismatches.append(f"  [{key}] in constants but missing from YAML: {sorted(missing)}")
        if extra:
            mismatches.append(f"  [{key}] extra in YAML not in constants: {sorted(extra)}")

    if mismatches:
        print("PARITY MISMATCH:")
        for m in mismatches:
            print(m)
        return False
    print(f"Parity check passed — all {len(all_keys)} domains match.")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Golden routing corpus generator")
    parser.add_argument(
        "--source",
        choices=["constants", "yaml"],
        default="constants",
        help=(
            "Authoritative source for corpus generation. "
            "'constants' (default) uses Python constants (authoritative until Slice 2C). "
            "'yaml' uses config/hermes-routing-triggers.yaml (for future migration testing)."
        ),
    )
    parser.add_argument(
        "--check-parity",
        action="store_true",
        help="Compare routing terms from constants vs YAML and report mismatches. Does not generate a corpus.",
    )
    args = parser.parse_args()

    if args.check_parity:
        ok = check_parity()
        sys.exit(0 if ok else 1)

    if args.source == "yaml":
        print(
            "WARNING: --source yaml generates corpus from YAML triggers (not authoritative). "
            "Python constants remain authoritative until Slice 2C. "
            "Do NOT commit a corpus generated with --source yaml as the golden corpus."
        )

    generate()
