from __future__ import annotations

import json

import pytest

from job_intel import calibration
from job_intel.models import Evaluation, Vacancy
from job_intel.store import JobIntelStore


@pytest.fixture()
def store(tmp_path):
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    return store


@pytest.fixture()
def scoring_yaml(tmp_path, monkeypatch):
    path = tmp_path / "scoring.yaml"
    path.write_text(
        "scoring:\n"
        "  positive_signals:\n"
        "    PnL_ownership: 25\n"
        "    remote_friendly: 5\n"
        "  negative_signals:\n"
        "    pure_project_management: -30\n"
        "  thresholds:\n"
        "    possible_fit: 60\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calibration, "SCORING_SEED_PATH", path)
    return path


def seed_feedback(store, detail_codes, count, *, company="Acme", data_quality=False):
    for index in range(count):
        event_id = store.create_feedback_event(
            slack_channel_id="C123",
            slack_message_ts=f"17600000{index:02d}.{abs(hash(tuple(detail_codes))) % 1000}",
            user_id="U1",
            reaction_type="-1",
            company=company,
        )
        store.update_feedback_event(
            event_id,
            status="classified",
            reason_category_codes_json=["data_quality_issue"] if data_quality else ["seniority_scope_mismatch"],
            reason_detail_codes_json=detail_codes,
            attribution_targets_json=["parser"] if data_quality else ["seniority_scope"],
            classifier_confidence=0.9,
            applies_to_company=False,
        )


def seed_evaluation(store, key, score, breakdown, *, positive=False):
    vacancy = Vacancy(
        source_id=key,
        title=f"Head of Product {key}",
        company="Acme",
        location="Remote",
        url=f"https://example.com/jobs/{key}",
        source="linkedin",
        description="Executive role",
    )
    vacancy_id = store.upsert_vacancy(vacancy, key)
    evaluation = Evaluation(
        score=score,
        tier="strong_fit" if score >= 75 else "possible_fit",
        recommendation="strong_fit" if score >= 75 else "potential_fit",
        raw_breakdown=breakdown,
    )
    store.save_evaluation(key, evaluation)
    store.insert_vacancy_slack_message(
        vacancy_id=vacancy_id,
        run_id=None,
        vacancy_key=key,
        canonical_url=f"https://example.com/jobs/{key}",
        card_key=f"card-{key}",
        notification_id=None,
        slack_channel="C123",
        slack_message_ts=f"1760001000.{abs(hash(key)) % 10000}",
        message_type="vacancy_card",
        company="Acme",
        title=f"Head of Product {key}",
        score=score,
        recommendation="strong_fit",
        url=f"https://example.com/jobs/{key}",
    )
    if positive:
        store.record_vacancy_feedback_event(
            vacancy_id=vacancy_id,
            run_id=None,
            notification_id=None,
            vacancy_key=key,
            canonical_url=f"https://example.com/jobs/{key}",
            card_key=f"card-{key}",
            slack_channel="C123",
            slack_message_ts=f"1760001000.{abs(hash(key)) % 10000}",
            feedback_type="interesting",
            event_type="reaction_added",
            event_timestamp="2026-07-01T00:00:00+00:00",
            user_id="U1",
        )
    return vacancy_id


# --- Slice 7: analytics ------------------------------------------------------


def test_aggregate_top_reasons_and_data_quality_split(store):
    seed_feedback(store, ["no_pnl_ownership"], 4)
    seed_feedback(store, ["bad_parse"], 2, data_quality=True)
    summary = calibration.aggregate_feedback(store, days=30)
    assert summary["total_negative_events"] == 6
    assert dict(summary["top_reason_detail_codes"])["no_pnl_ownership"] == 4
    assert summary["preference_events"] == 4
    assert summary["data_quality_events"] == 2


def test_aggregate_by_company(store):
    seed_feedback(store, ["no_pnl_ownership"], 3, company="Globex")
    summary = calibration.aggregate_feedback(store, days=30, company="Globex")
    assert summary["total_negative_events"] == 3
    assert dict(summary["negative_by_company"])["Globex"] == 3


# --- Slice 8: digest -----------------------------------------------------------


def test_weekly_digest_short_mode_below_threshold(store):
    seed_feedback(store, ["no_pnl_ownership"], 2)
    digest = calibration.build_weekly_digest(store)
    assert digest["mode"] == "short"


def test_weekly_digest_full_mode(store):
    seed_feedback(store, ["no_pnl_ownership"], 5)
    seed_feedback(store, ["bad_parse"], 2, data_quality=True)
    digest = calibration.build_weekly_digest(store)
    assert digest["mode"] == "full"
    assert "no_pnl_ownership" in digest["text"]
    assert "data-quality" in digest["text"]


# --- Slice 9: proposals ---------------------------------------------------------


def test_no_proposal_from_one_off_feedback(store, scoring_yaml):
    seed_feedback(store, ["no_pnl_ownership"], 1)
    result = calibration.generate_proposal(store)
    assert result["status"] == "no_proposal"


def test_data_quality_pattern_never_generates_proposal(store, scoring_yaml):
    seed_feedback(store, ["bad_parse"], 10, data_quality=True)
    result = calibration.generate_proposal(store)
    assert result["status"] == "no_proposal"


def test_repeated_pattern_generates_proposal_with_evidence(store, scoring_yaml):
    seed_feedback(store, ["no_pnl_ownership"], 6)
    result = calibration.generate_proposal(store)
    assert result["status"] == "proposed"
    proposal = store.get_scoring_proposal(result["proposal_id"])
    assert proposal["status"] == "proposed"
    assert proposal["evidence"]["total_negative_events"] == 6
    changes = proposal["proposed_changes"]
    assert changes[0]["scoring_feature"] == "PnL_ownership"
    assert changes[0]["current_value"] == 25
    assert changes[0]["proposed_value"] > 25
    # proposal must not touch the config
    assert "PnL_ownership: 25" in scoring_yaml.read_text()


def test_hard_blocker_threshold_14_days(store, scoring_yaml):
    seed_feedback(store, ["onsite_required"], 3)
    result = calibration.generate_proposal(store)
    assert result["status"] == "proposed"
    features = [change["scoring_feature"] for change in result["proposed_changes"]]
    assert "remote_friendly" in features


# --- Slice 10: dry-run ------------------------------------------------------------


def make_proposal(store, scoring_yaml):
    seed_feedback(store, ["no_pnl_ownership"], 6)
    result = calibration.generate_proposal(store)
    assert result["status"] == "proposed"
    return result["proposal_id"]


def test_dry_run_reports_threshold_crossings(store, scoring_yaml):
    proposal_id = make_proposal(store, scoring_yaml)
    # with PnL bonus 25 -> 31: 55 + 25*(31/25-1) = 61 crosses up
    seed_evaluation(store, "rise", 55, {"PnL_ownership": 25})
    seed_evaluation(store, "flat", 50, {"other_signal": 10})
    result = calibration.dry_run_proposal(store, proposal_id)
    assert result["status"] == "ok"
    assert result["sample_size"] == 2
    assert result["scores_changed"] == 1
    assert result["would_rise_above_threshold"] == 1
    assert result["would_drop_below_threshold"] == 0
    proposal = store.get_scoring_proposal(proposal_id)
    assert proposal["dry_run_result"]["sample_size"] == 2


def test_dry_run_detects_harm_to_positive_history(store, scoring_yaml, monkeypatch):
    proposal_id = make_proposal(store, scoring_yaml)
    proposal = store.get_scoring_proposal(proposal_id)
    # force a negative change so a liked vacancy drops materially
    changes = proposal["proposed_changes"]
    changes[0]["proposed_value"] = 5  # 25 -> 5 slashes PnL contribution
    store.update_scoring_proposal(proposal_id, status="proposed")
    monkeypatch.setattr(
        calibration, "_sample_evaluations",
        lambda *args, **kwargs: [
            {
                "vacancy_key": "liked",
                "score": 80,
                "breakdown": {"PnL_ownership": 25},
                "company": "Acme",
                "title": "CPO",
                "positive": True,
            }
        ],
    )
    store_proposal = store.get_scoring_proposal(proposal_id)
    store_proposal["proposed_changes"] = changes
    monkeypatch.setattr(store, "get_scoring_proposal", lambda pid: store_proposal)
    result = calibration.dry_run_proposal(store, proposal_id)
    assert result["previously_positive_opportunities_harmed"] == 1
    assert result["risk_level"] == "high"


# --- Slice 11: apply/reject/rollback ------------------------------------------------


def test_apply_requires_dry_run(store, scoring_yaml):
    proposal_id = make_proposal(store, scoring_yaml)
    result = calibration.apply_proposal(store, proposal_id, actor="denis")
    assert result["status"] == "dry_run_required"


def test_apply_after_dry_run_patches_config_and_rollback_restores(store, scoring_yaml):
    proposal_id = make_proposal(store, scoring_yaml)
    seed_evaluation(store, "sample", 70, {"PnL_ownership": 25})
    calibration.dry_run_proposal(store, proposal_id)

    applied = calibration.apply_proposal(store, proposal_id, actor="denis")
    assert applied["status"] == "applied"
    text = scoring_yaml.read_text()
    assert "PnL_ownership: 31" in text

    proposal = store.get_scoring_proposal(proposal_id)
    assert proposal["status"] == "applied"
    assert proposal["rollback_ref"]

    rolled = calibration.rollback_proposal(store, proposal_id, actor="denis")
    assert rolled["status"] == "rolled_back"
    assert "PnL_ownership: 25" in scoring_yaml.read_text()

    events = [event["event_type"] for event in store.fetch_scoring_calibration_events(proposal_id)]
    assert events == ["proposed", "dry_run", "applied", "rolled_back"]


def test_high_risk_proposal_blocked_without_force(store, scoring_yaml, monkeypatch):
    proposal_id = make_proposal(store, scoring_yaml)
    seed_evaluation(store, "sample", 70, {"PnL_ownership": 25})
    calibration.dry_run_proposal(store, proposal_id)
    store.update_scoring_proposal(proposal_id, risk_level="high")
    blocked = calibration.apply_proposal(store, proposal_id, actor="denis")
    assert blocked["status"] == "blocked_high_risk"
    forced = calibration.apply_proposal(store, proposal_id, actor="denis", force=True)
    assert forced["status"] == "applied"


def test_reject_preserves_proposal(store, scoring_yaml):
    proposal_id = make_proposal(store, scoring_yaml)
    rejected = calibration.reject_proposal(store, proposal_id, actor="denis")
    assert rejected["status"] == "rejected"
    proposal = store.get_scoring_proposal(proposal_id)
    assert proposal["status"] == "rejected"
    assert proposal["proposed_changes"]  # preserved
