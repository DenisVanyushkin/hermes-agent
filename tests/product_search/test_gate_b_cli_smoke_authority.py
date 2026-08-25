from __future__ import annotations

import json

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


def test_corpus_authority_file_is_consumed_and_tampering_fails_closed(tmp_path):
    authority = tmp_path / "corpus-authority.json"
    expected = "1" * 64
    authority.write_bytes(_authority_bytes(expected))

    assert fixture._load_corpus_authority(
        authority,
        expected_corpus_sha256=expected,
    ) == expected

    authority.write_bytes(_authority_bytes("2" * 64))
    with pytest.raises(ValueError, match="corpus_authority_mismatch"):
        fixture._load_corpus_authority(
            authority,
            expected_corpus_sha256=expected,
        )
