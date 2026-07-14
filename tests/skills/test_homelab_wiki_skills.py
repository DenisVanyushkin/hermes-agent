from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_homelab_wiki_client_is_index_first_and_read_only() -> None:
    content = (
        ROOT / "skills/knowledge/homelab-wiki-client/SKILL.md"
    ).read_text(encoding="utf-8")
    lowered = content.casefold()
    assert "wiki/_index.md" in content
    assert "read-only" in lowered
    assert "immutable citation" in lowered
    assert "freshness" in lowered
    assert "contradiction" in lowered
    assert "never edit" in lowered


def test_homelab_wiki_enrichment_requires_authority_and_draft_pr() -> None:
    content = (
        ROOT / "skills/knowledge/homelab-wiki-enrichment-proposal/SKILL.md"
    ).read_text(encoding="utf-8")
    lowered = content.casefold()
    for phrase in (
        "explicit user authorization",
        "approved schedule",
        "draft pr",
        "human-attention notification",
        "never merge",
    ):
        assert phrase in lowered


def test_skills_use_published_vault_without_private_runtime_data() -> None:
    contents = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "skills/knowledge").glob("homelab-wiki-*/SKILL.md"))
    )
    assert "${HOMELAB_WIKI_PATH:-/srv/knowledge/current}" in contents
    assert ".hermes/memories" not in contents
    assert "sessions.db" not in contents
