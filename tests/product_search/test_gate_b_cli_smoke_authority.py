from __future__ import annotations

import json
from pathlib import Path

import pytest

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
