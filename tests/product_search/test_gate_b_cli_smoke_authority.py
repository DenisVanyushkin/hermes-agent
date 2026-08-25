from __future__ import annotations

import json
from pathlib import Path

import pytest

import job_intel.product_search.gate_b_evidence_runner_v1 as runner
from tests.product_search import gate_b_cli_smoke_fixture as fixture


def _authority_bytes(corpus_sha256: str) -> bytes:
    return json.dumps(
        {
            "schema_version": fixture.CORPUS_AUTHORITY_SCHEMA,
            "corpus_sha256": corpus_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _computed_corpus_sha256() -> str:
    rows = [
        {"ordinal": index, "record": record, "raw": raw}
        for index in range(48)
        for record, raw in (fixture._make_record(index),)
    ]
    return fixture._sha(fixture._canonical(rows))


def test_prepare_consumes_configured_authority_and_fails_closed_on_tampering(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "corpus-authority.json"
    expected = _computed_corpus_sha256()
    authority.write_bytes(_authority_bytes(expected))
    repo_root = Path(__file__).resolve().parents[2]

    manifest_path, _, _ = fixture.prepare(
        root=tmp_path / "first-fixture",
        artifact_root=tmp_path / ("a" * 64),
        repo_root=repo_root,
        corpus_authority_path=authority,
    )
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["corpus_sha256"] == expected

    authority.write_bytes(_authority_bytes("2" * 64))
    with pytest.raises(ValueError, match="corpus_authority_mismatch"):
        fixture.prepare(
            root=tmp_path / "tampered-fixture",
            artifact_root=tmp_path / ("b" * 64),
            repo_root=repo_root,
            corpus_authority_path=authority,
        )


def test_smoke_names_decision_evidence_rows_truthfully() -> None:
    source = Path("scripts/gate_b_composition_smoke.py").read_text()
    assert "decision_evidence_rows" in source
    assert "decision_request_factory_calls" not in source


def test_corrupt_decision_evidence_is_not_missing_factory_evidence(
    tmp_path: Path,
) -> None:
    ref = runner.ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="a" * 64,
        ordinal=0,
        input_sha256="b" * 64,
        projection_sha256="c" * 64,
    )
    store = runner.DecisionEvidenceStore(tmp_path)
    store.save_exclusive(ref, b"{}")
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_bytes())
    payload["decision_b64"] = "%%%"
    path.write_bytes(runner._canonical_bytes(payload))

    with pytest.raises(ValueError, match="decision evidence is invalid") as caught:
        store.find_for_manifest_ref(ref)
    assert not isinstance(caught.value, runner.DecisionEvidenceMissingError)
