"""Golden fixture generator — provenance record for the Step 2 dataset.

Reads a read-only dump of the live vacancies table (produced 2026-07-19 from
/var/lib/job-intel/state/job_intel.sqlite3, see fixture `vacancy_identity`)
plus manual gold annotations encoded below, and writes one YAML fixture per
case. Gold documents are validated against the canonical model at generation
time. Copyright: only bounded excerpts of source text are stored, never full
postings.

Run (canonical host):
    venv/bin/python tests/fixtures/vacancy_understanding/_generate_fixtures.py \
        /tmp/step2_fixture_source.json

Dataset version: 1.0.0
Annotation source: manual gold annotation by the operator-approved research
corpus (docs/audit/2026-07-19-career-preference-model.md) — candidate-
independent semantics only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

from job_intel.vacancy_understanding.model import VacancyUnderstanding

DATASET_VERSION = "1.0.0"
OUT_DIR = Path(__file__).parent
GOLD_DATE = "2026-07-19T00:00:00Z"

SRC_TEXT = "src_vacancy_text"
SRC_STRUCT = "src_structured_fields"
SRC_GOLD = "src_gold_annotation"

REGISTRY = [
    {"id": SRC_TEXT, "source_type": "vacancy_text",
     "description": "bounded excerpts of the stored vacancy description"},
    {"id": SRC_STRUCT, "source_type": "structured_source_field",
     "description": "structured source row fields (title/location/company)"},
    {"id": SRC_GOLD, "source_type": "manual_gold_annotation",
     "description": "manual gold annotation 2026-07-19 grounded in the research corpus"},
]


def ev(excerpt=None, rationale=None, src=SRC_GOLD, stype="manual_gold_annotation", loc=None):
    e = {"source_id": src, "source_type": stype}
    if excerpt:
        e["excerpt"] = excerpt[:400]
    if loc:
        e["location"] = loc
    if rationale:
        e["rationale"] = rationale
    return e


def fact(value, conf="high", *, excerpt=None, rationale=None, loc=None):
    return {
        "value": value, "confidence": conf, "method": "manual_gold_annotation",
        "evidence": [ev(excerpt=excerpt, rationale=rationale, loc=loc)],
    }


def title_fact(value, conf, title):
    return {
        "value": value, "confidence": conf, "method": "manual_gold_annotation",
        "evidence": [ev(excerpt=title, loc="title",
                        rationale="title is evidence, not final scope truth")],
    }


def excerpts_for(text: str, patterns: list[str], radius=120, limit=6) -> list[str]:
    out = []
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            out.append(text[max(0, m.start() - radius): m.end() + radius].strip())
        if len(out) >= limit:
            break
    return out


def clean(text: str) -> str:
    import html
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Fixture specs. gold_* dicts are partial VacancyUnderstanding sections.
# det = deterministic replay expectations (dot-path -> value).
# ---------------------------------------------------------------------------

def spec_list(rows):
    def r(db_id):
        return rows[db_id]

    T = lambda db_id: r(db_id)["title"]
    S = []

    # 1. Wise APAC — the reference broad-mandate case (title-only snapshot)
    S.append(dict(
        fid="wise_apac_growth_expansion", db=5919, synthetic=False,
        pats=[],
        gold_mandate={
            "scope_breadth": title_fact("region", "medium", T(5919)),
            "growth_mandate": title_fact("true", "high", T(5919)),
            "expansion_mandate": title_fact("true", "high", T(5919)),
            "revenue_proximity": fact("direct_revenue", "medium",
                                      rationale="growth & expansion ownership of a whole region implies revenue accountability"),
            "mandate_summary": fact("Regional growth and market-expansion ownership across APAC.",
                                    "medium", rationale="synthesized from title; description snapshot incomplete"),
        },
        gold_company={"scale": fact("global", "high", rationale="Wise operates globally (public company)"),
                      "is_crypto_exchange": fact("false", "high", rationale="Wise is a cross-border payments company")},
        det={
            "feasibility_facts.country.value": "Singapore",
            "feasibility_facts.country_group.value": "other",
        },
        critical=["scope_breadth == region", "growth_mandate == true",
                  "broad mandate: region-level ownership"],
        ambiguities=["description snapshot is title-only; semantic facts rely on title evidence"],
    ))

    # 2-3. Wise Pricing / Acquiring — narrow but monetization-core
    for fid, db in (("wise_pricing", 1728), ("wise_acquiring", 4510)):
        which = "pricing" if db == 1728 else "acquiring"
        S.append(dict(
            fid=fid, db=db, synthetic=False, pats=[],
            gold_mandate={
                "scope_breadth": title_fact("domain", "high", T(db)),
                "monetization_core": title_fact("true", "high", T(db)),
                f"{which}_core": title_fact("true", "high", T(db)),
                "growth_mandate": fact("unknown", "unknown", rationale="no evidence in snapshot"),
            },
            det={"feasibility_facts.country.value": "London" if False else None},
            critical=[f"scope_breadth == domain", "monetization_core == true",
                      "narrow scope but commercially central"],
            ambiguities=["title-only snapshot"],
        ))

    # 4. Wise Financial Crime — narrow, risk-heavy, NO broad-mandate inference
    S.append(dict(
        fid="wise_financial_crime", db=1813, synthetic=False, pats=[],
        gold_mandate={
            "scope_breadth": title_fact("domain", "high", T(1813)),
            "risk_compliance_heavy": title_fact("true", "high", T(1813)),
            "growth_mandate": fact("unknown", "unknown", rationale="no evidence; must not be inferred from company"),
            "monetization_core": fact("false", "medium", rationale="financial-crime compliance domain, not monetization"),
        },
        det={},
        critical=["scope_breadth == domain (never region/business_line)",
                  "no broad-mandate inference from the Wise brand"],
        ambiguities=["senior-manager compliance role used as the Financial Crime contrast (no product-director FinCrime posting in DB)"],
    ))

    # 5. Wise Onboarding — feature scope
    S.append(dict(
        fid="wise_onboarding_experience", db=1773, synthetic=False, pats=[],
        gold_mandate={
            "scope_breadth": title_fact("feature", "medium", T(1773)),
            "feature_delivery_only": fact("unknown", "unknown", rationale="title-only snapshot"),
            "growth_mandate": fact("unknown", "unknown", rationale="no evidence"),
        },
        det={},
        critical=["scope_breadth == feature (narrow onboarding experience)"],
        ambiguities=["feature vs narrow domain — annotated feature per research contrast"],
    ))

    # 6. Airwallex GPNI — platform-as-business, NOT platform engineering
    S.append(dict(
        fid="airwallex_gpni", db=1262, synthetic=False,
        pats=[r"zero-to-one", r"true ownership", r"founder", r"26 offices",
              r"payments? (network|infrastructure)", r"treasury|payouts|FX"],
        gold_mandate={
            "scope_breadth": fact("business_line", "high",
                                  excerpt="Head of Product, Global Payments Network Infrastructure",
                                  rationale="owns the money-movement platform core — a business line, not a feature"),
            "platform_as_business": fact("true", "high",
                                         rationale="the payments network IS the business: money moves through it (collect/treasury/payouts/FX rails)"),
            "platform_engineering": fact("false", "high",
                                         rationale="'Infrastructure' in the title denotes the commercial platform product, not DevEx/internal platform engineering"),
            "zero_to_one_mandate": fact("true", "medium", rationale="posting language: zero-to-one, true ownership"),
            "strategy_ownership": fact("true", "medium", rationale="head-of-product ownership language"),
            "revenue_proximity": fact("direct_revenue", "medium",
                                      rationale="platform rails monetized directly"),
            "mandate_summary": fact("Ownership of the global payments-network platform that constitutes the company's core money-movement business.",
                                    "high", rationale="synthesis of posting"),
        },
        gold_company={"scale": fact("global", "high", excerpt="26 offices around the globe"),
                      "platform_ecosystem": fact("true", "high", rationale="platform is the product"),
                      "is_crypto_exchange": fact("false", "high", rationale="B2B payments platform")},
        det={"feasibility_facts.country.value": "Singapore",
             "feasibility_facts.country_group.value": "other"},
        critical=["platform_as_business == true", "platform_engineering == false",
                  "'infrastructure' word alone does not flip to platform_engineering"],
        ambiguities=[],
    ))

    # 7. Airwallex Payment Fraud — narrow risk domain, same company
    S.append(dict(
        fid="airwallex_payment_fraud", db=1287, synthetic=False,
        pats=[r"fraud", r"risk", r"26 offices"],
        gold_mandate={
            "scope_breadth": fact("domain", "high", excerpt="Product Director, Payment Fraud",
                                  rationale="single risk domain inside the platform"),
            "risk_compliance_heavy": fact("true", "high", rationale="payment-fraud prevention domain"),
            "platform_as_business": fact("unknown", "unknown",
                                         rationale="company platform shape must not transfer to a narrow fraud role by association"),
            "growth_mandate": fact("false", "medium", rationale="no growth/expansion language"),
        },
        det={"feasibility_facts.country.value": "Singapore"},
        critical=["scope_breadth == domain", "risk_compliance_heavy == true",
                  "platform_as_business NOT inherited from company (unknown)"],
        ambiguities=[],
    ))

    # 8. Monzo Business Banking — business line
    S.append(dict(
        fid="monzo_business_banking", db=7518, synthetic=False,
        pats=[r"sponsor visas", r"relocate to the UK", r"[Hh]ybrid", r"business banking", r"customers"],
        gold_mandate={
            "scope_breadth": fact("business_line", "high",
                                  excerpt="Senior Product Director, Business Banking",
                                  rationale="a whole customer business line (SMB banking)"),
            "revenue_proximity": fact("direct_revenue", "medium",
                                      rationale="business line with its own P&L-like revenue accountability"),
        },
        gold_company={"customer_model": fact("smb_mass", "medium", rationale="mass SMB banking line of a B2C bank")},
        det={"feasibility_facts.sponsorship_stated.value": "yes",
             "feasibility_facts.relocation_support.value": "explicit",
             "feasibility_facts.work_format.value": "hybrid"},
        critical=["scope_breadth == business_line"],
        ambiguities=[],
    ))

    # 9. Monzo Flex (Borrowing) — narrower product/domain
    S.append(dict(
        fid="monzo_flex_borrowing", db=7498, synthetic=False,
        pats=[r"sponsor visas", r"relocate to the UK", r"[Hh]ybrid", r"Flex", r"borrow"],
        gold_mandate={
            "scope_breadth": fact("domain", "high", excerpt="Product Director, Flex (Borrowing)",
                                  rationale="one lending product, narrower than a business line"),
        },
        det={"feasibility_facts.sponsorship_stated.value": "yes"},
        critical=["scope_breadth == domain (narrower than business_banking fixture)"],
        ambiguities=[],
    ))

    # 10. Brex Growth/AI (Vancouver) — growth mandate
    S.append(dict(
        fid="brex_growth_ai_vancouver", db=7064, synthetic=False,
        pats=[r"office at least \d+ days", r"[Aa]cquisition", r"onboarding", r"AI"],
        gold_mandate={
            "scope_breadth": fact("domain", "medium",
                                  excerpt="Director of Product, Growth/AI",
                                  rationale="growth funnel (acquisition+onboarding) — broad domain, below a business line"),
            "growth_mandate": title_fact("true", "high", T(7064)),
        },
        det={"feasibility_facts.country.value": "Canada",
             "feasibility_facts.country_group.value": "other",
             "feasibility_facts.work_format.value": "hybrid"},
        critical=["growth_mandate == true"],
        ambiguities=["scope domain-vs-business_line is genuinely ambiguous; annotated domain"],
    ))

    # 11. Affirm Senior Director PM — Remote US, sponsorship unknown
    S.append(dict(
        fid="affirm_senior_director_pm_remote_us", db=9581, synthetic=False,
        pats=[r"remote-first", r"#LI-Remote", r"work almost anywhere"],
        gold_mandate={
            "scope_breadth": fact("portfolio", "medium",
                                  excerpt="Senior Director, Product Management",
                                  rationale="broad senior product-management remit"),
        },
        det={"feasibility_facts.work_format.value": "remote",
             "feasibility_facts.country_group.value": "usa",
             "feasibility_facts.sponsorship_stated.value": "unknown"},
        critical=["remote fact recorded; sponsorship stays unknown as a FACT",
                  "no onsite-sponsorship conclusion is drawn"],
        ambiguities=[],
    ))

    # 12. Coinbase Core Infrastructure — platform ENGINEERING
    S.append(dict(
        fid="coinbase_core_infrastructure", db=7558, synthetic=False,
        pats=[r"remote-first", r"[Ii]nfrastructure", r"reliability"],
        gold_mandate={
            "scope_breadth": fact("domain", "high",
                                  excerpt="Group Product Manager, Core Infrastructure & Reliability",
                                  rationale="internal platform/reliability domain"),
            "platform_engineering": fact("true", "high",
                                         rationale="core infrastructure & reliability = platform engineering, serving internal delivery"),
            "platform_as_business": fact("false", "high",
                                         rationale="the infrastructure is not the monetized product itself"),
        },
        gold_company={"is_crypto_exchange": fact("true", "high",
                                                 rationale="Coinbase is a crypto exchange (company fact, not a verdict)")},
        det={"feasibility_facts.work_format.value": "remote",
             "feasibility_facts.country_group.value": "usa"},
        critical=["platform_engineering == true", "platform_as_business == false",
                  "crypto employer is a company FACT, no barrier is asserted"],
        ambiguities=[],
    ))

    # 13. OKX KYB Onboarding — Mandarin barrier + crypto employer (distinct facts)
    S.append(dict(
        fid="okx_kyb_onboarding_mandarin_barrier", db=7315, synthetic=False,
        pats=[r"Fluent in English and Mandarin", r"KYB|onboarding", r"crypto|exchange"],
        gold_mandate={
            "scope_breadth": fact("feature", "high",
                                  excerpt="Principal / Senior Product Manager, Institution Onboarding Experience (KYB)",
                                  rationale="single onboarding-experience flow"),
        },
        gold_company={"is_crypto_exchange": fact("true", "high", rationale="OKX is a crypto exchange")},
        gold_requirements={
            "entry_barriers": [{
                "requirement": "Fluent in English and Mandarin",
                "transferability": "non_transferable_barrier",
                "why": "Mandarin fluency is mandatory and cannot be acquired for hiring purposes",
                "confidence": "high",
                "evidence": [ev(excerpt="Fluent in English and Mandarin.", src=SRC_TEXT,
                                stype="vacancy_text", loc="description:requirements")],
            }],
            "overall_transferability": fact("non_transferable_barrier", "high",
                                            rationale="mandatory language requirement"),
        },
        det={"feasibility_facts.country_group.value": "other"},
        critical=["barrier arises from the mandatory language requirement, NOT from crypto context",
                  "is_crypto_exchange and the barrier are separate facts"],
        ambiguities=[],
    ))

    # 14. Block Head of Strategic Product Sales — sales function
    S.append(dict(
        fid="block_strategic_product_sales", db=8897, synthetic=False,
        pats=[r"[Ss]ales", r"Cash App"],
        gold_role={"function_families": ["sales"]},
        gold_mandate={
            "digital_business_ownership": fact("false", "medium",
                                               rationale="sales quota role, no digital business/product ownership"),
        },
        det={"feasibility_facts.country_group.value": "usa"},
        critical=["function is sales; hybrid title families recorded without forcing one category"],
        ambiguities=["title mixes product+sales; families keep both, function annotated sales-dominant"],
    ))

    # 15. Canva FP&A — finance support function (title-only)
    S.append(dict(
        fid="canva_fpna", db=5924, synthetic=False, pats=[],
        gold_role={"function_families": ["finance"]},
        gold_mandate={
            "scope_breadth": fact("domain", "medium", rationale="corporate FP&A function"),
            "digital_business_ownership": fact("false", "high",
                                               rationale="finance support function, no digital business ownership"),
        },
        det={},
        critical=["function is finance support"],
        ambiguities=["title-only snapshot"],
    ))

    # 16. KZ local role — factual KZ + sponsorship unknown is VALID
    S.append(dict(
        fid="kz_local_zeekr_almaty", db=54, synthetic=False, pats=[],
        gold_mandate={},
        det={"feasibility_facts.country.value": "Kazakhstan",
             "feasibility_facts.country_group.value": "kazakhstan",
             "feasibility_facts.work_format.value": "onsite",
             "feasibility_facts.local_market_indicator.value": "true",
             "feasibility_facts.sponsorship_stated.value": "unknown"},
        critical=["kazakhstan + sponsorship unknown is a valid factual combination",
                  "local_market_indicator == true", "no contradiction risk emitted"],
        ambiguities=["global automaker brand hiring for the local KZ market; local_market refers to the ROLE'S market"],
    ))

    # 17. Remote role with large timezone difference (facts only)
    S.append(dict(
        fid="affirm_remote_us_timezone_gap", db=7740, synthetic=False,
        pats=[r"sponsorship is not available", r"remote-first", r"#LI-Remote"],
        gold_mandate={},
        det={"feasibility_facts.work_format.value": "remote",
             "feasibility_facts.country_group.value": "usa",
             "feasibility_facts.sponsorship_stated.value": "no"},
        critical=["timezone burden is at most a factual risk, never a rejection",
                  "explicit 'sponsorship not available' extracted as sponsorship == no"],
        ambiguities=["timezone_expectations not stated in text — stays unknown"],
    ))

    # 18. Onsite non-US role, sponsorship unknown (Israel)
    S.append(dict(
        fid="payoneer_core_ai_platform_israel", db=8758, synthetic=False,
        pats=[r"Hybrid", r"AI-nativ", r"platform"],
        gold_mandate={},
        det={"feasibility_facts.country.value": "Israel",
             "feasibility_facts.country_group.value": "other",
             "feasibility_facts.work_format.value": "hybrid",
             "feasibility_facts.sponsorship_stated.value": "unknown"},
        critical=["non-US onsite/hybrid with unknown sponsorship — factual record only"],
        ambiguities=[],
    ))

    # 19. US onsite (hybrid-office) without explicit sponsorship
    S.append(dict(
        fid="brex_growth_ai_sf_no_sponsorship", db=7063, synthetic=False,
        pats=[r"office at least \d+ days", r"San Francisco"],
        gold_mandate={"growth_mandate": title_fact("true", "high", T(7063))},
        det={"feasibility_facts.country.value": "United States",
             "feasibility_facts.country_group.value": "usa",
             "feasibility_facts.work_format.value": "hybrid",
             "feasibility_facts.sponsorship_stated.value": "unknown"},
        critical=["US office role without sponsorship statement: factual extraction only, no rejection verdict"],
        ambiguities=[],
    ))

    # 21. OKX Internal HR & Finance Systems — internal tools / back-office
    S.append(dict(
        fid="okx_internal_hr_finance", db=7332, synthetic=False,
        pats=[r"do not require OKX", r"right to work in Hong Kong", r"Chinese and English",
              r"[Ii]nternal", r"HR|[Ff]inance [Ss]ystems"],
        gold_mandate={
            "internal_tools_backoffice": fact("true", "high",
                                              excerpt="Product Director, Internal HR & Finance Systems",
                                              rationale="internal employee-facing HR/finance tooling"),
            "scope_breadth": fact("domain", "medium", rationale="internal systems domain"),
        },
        gold_company={"is_crypto_exchange": fact("true", "high", rationale="OKX is a crypto exchange")},
        det={"feasibility_facts.must_be_already_authorized.value": "true",
             "feasibility_facts.sponsorship_stated.value": "no"},
        critical=["internal_tools_backoffice == true",
                  "'no sponsorship, right-to-work required' extracted as facts"],
        ambiguities=[],
    ))

    return S


SYNTHETIC_US_SPONSORSHIP = dict(
    fid="synthetic_us_onsite_with_sponsorship", db=None, synthetic=True,
    raw=dict(
        vacancy_key="synthetic:policy-control:us-onsite-sponsorship",
        source_system="synthetic_fixture",
        source_record_id=None,
        company="PolicyControl Co (synthetic)",
        title="Director of Product, Payments Platform",
        location="New York, United States",
        description=(
            "Synthetic policy-control fixture (not a real posting). "
            "We provide visa sponsorship and relocation support for this role. "
            "This position is based in our New York office."
        ),
    ),
    gold_mandate={},
    det={"feasibility_facts.country.value": "United States",
         "feasibility_facts.country_group.value": "usa",
         "feasibility_facts.sponsorship_stated.value": "yes",
         "feasibility_facts.relocation_support.value": "explicit"},
    critical=["explicit US sponsorship extracted as sponsorship == yes",
              "synthetic fixture is labeled and must never enter behavioral metrics"],
    ambiguities=["no real US-onsite-with-sponsorship posting existed in the DB; synthetic by design"],
)


def build_gold_doc(spec, raw, det_doc: dict) -> dict:
    """Start from the deterministic extractor structure and overlay gold."""
    doc = det_doc  # already a plain dict of a validated model
    doc["metadata"]["is_synthetic_fixture"] = bool(spec["synthetic"])
    for section_key, gold_key in (
        ("mandate", "gold_mandate"), ("company", "gold_company"),
        ("requirements", "gold_requirements"),
    ):
        for field, value in (spec.get(gold_key) or {}).items():
            doc[section_key][field] = value
    for field, value in (spec.get("gold_role") or {}).items():
        doc["role_identity"][field] = value
    # registry: extractor ids + gold id
    have = {d["id"] for d in doc["evidence_registry"]}
    for entry in REGISTRY:
        if entry["id"] not in have:
            doc["evidence_registry"].append(entry)
    # normalize extractor source ids to fixture registry ids
    return doc


def main(dump_path: str):
    from job_intel.vacancy_understanding.extractor import RawVacancy, extract
    from datetime import datetime, timezone

    rows = {r["id"]: r for r in json.load(open(dump_path))}
    created = datetime(2026, 7, 19, tzinfo=timezone.utc)
    specs = spec_list(rows) + [SYNTHETIC_US_SPONSORSHIP]
    for spec in specs:
        if spec["db"] is not None:
            row = rows[spec["db"]]
            raw = dict(
                vacancy_key=row["vacancy_key"], source_system=row["source"],
                source_record_id=str(row["source_id"]) if row.get("source_id") else None,
                company=row["company"], title=row["title"],
                location=row.get("location"), description=row.get("description") or "",
            )
        else:
            raw = dict(spec["raw"])

        text = clean(raw["description"])
        head = text[:300]
        excerpts = [head] if head else []
        excerpts += excerpts_for(text, spec.get("pats") or [])
        replay_description = " … ".join(dict.fromkeys(excerpts))

        replay_raw = dict(raw, description=replay_description)
        result = extract(RawVacancy(**replay_raw), created_at=created)
        det_doc = json.loads(result.model_dump_json())

        gold_doc = build_gold_doc(spec, raw, det_doc)
        VacancyUnderstanding.model_validate(gold_doc)  # gold must be valid

        # verify deterministic expectations hold at generation time
        for path, expected in (spec.get("det") or {}).items():
            if expected is None:
                continue
            node = det_doc
            for part in path.split("."):
                node = node[part]
            assert node == expected, f"{spec['fid']}: {path} = {node!r} != {expected!r}"

        fixture = {
            "fixture_id": spec["fid"],
            "dataset_version": DATASET_VERSION,
            "is_synthetic": bool(spec["synthetic"]),
            "annotation_source": (
                "manual gold annotation 2026-07-19; grounded in "
                "docs/audit/2026-07-19-career-preference-model.md; candidate-independent"
            ),
            "vacancy_identity": {
                "db_id": spec["db"],
                "vacancy_key": raw["vacancy_key"],
                "company": raw["company"],
                "title": raw["title"],
                "location": raw.get("location"),
                "source_system": raw["source_system"],
                "source_content_sha16": hashlib.sha256(
                    (raw["description"] or "").encode()).hexdigest()[:16],
                "source_text_full_length": len(text),
            },
            "replay_input": replay_raw,
            "deterministic_expected": {k: v for k, v in (spec.get("det") or {}).items()
                                       if v is not None},
            "critical_assertions": spec["critical"],
            "ambiguities": spec["ambiguities"],
            "vacancy_understanding": gold_doc,
        }
        out = OUT_DIR / f"{spec['fid']}.yaml"
        out.write_text(yaml.safe_dump(fixture, allow_unicode=True, sort_keys=False, width=100),
                       encoding="utf-8")
        print("wrote", out.name)


if __name__ == "__main__":
    main(sys.argv[1])
