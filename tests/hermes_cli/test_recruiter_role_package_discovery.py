from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.prompt_builder import build_skills_system_prompt
from hermes_cli.role_packages import (
    RolePackageError,
    build_repo_role_package_skill_context,
    discover_repo_role_packages,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RECRUITER_DIR = REPO_ROOT / "role-packages" / "recruiter"


def _write_role_package(tmp_path: Path, *, docs: dict[str, str] | None = None) -> Path:
    package_dir = tmp_path / "custom-role"
    (package_dir / "skills" / "custom-skill").mkdir(parents=True)
    (package_dir / "bundles").mkdir(parents=True)
    (package_dir / "role-package.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "package": {"name": "custom-role", "version": "0.1.0"},
                "role": {
                    "id": "custom_role",
                    "canonical_id": "custom_role",
                    "display_name": "Custom Role",
                    "role_family": "test",
                    "purpose_summary": "Test-only package.",
                    "persona": "Test persona.",
                    "routing": {"triggers": {"en": ["custom role"]}},
                    "tools": {"allowed_categories": ["read_only_inspection"]},
                },
                "boundary_mode": "observe_warn",
                "env_requires": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package_dir / "skills" / "custom-skill" / "SKILL.md").write_text(
        """---
name: custom-skill
description: Custom package skill.
metadata:
  hermes:
    tags: [custom]
---

# Custom Skill

## Boundaries

- Stay read only.

## Required Inputs

- Input.

## Expected Outputs

- Output.

## Failure Behavior

- Return CONTROLLED_ERROR.
""",
        encoding="utf-8",
    )
    (package_dir / "bundles" / "custom-bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "custom-bundle",
                "description": "Custom bundle.",
                "skills": ["custom-skill"],
                "expected_output": "CustomPacket",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if docs is not None:
        docs_dir = package_dir / "docs"
        docs_dir.mkdir()
        for name, content in docs.items():
            (docs_dir / name).write_text(content, encoding="utf-8")
    return package_dir


class TestRecruiterRolePackageDiscovery:
    def test_repo_role_package_is_discoverable(self) -> None:
        package_dirs = discover_repo_role_packages(REPO_ROOT)
        assert RECRUITER_DIR in package_dirs

    def test_recruiter_skill_context_contains_expected_metadata(self) -> None:
        payload = build_repo_role_package_skill_context(RECRUITER_DIR)

        assert payload["package_id"] == "hermes-recruiter"
        assert payload["role_id"] == "hermes_recruiter"
        assert [item["id"] for item in payload["skills"]] == [
            "company-assessment",
            "company-research",
            "company-risk-register",
            "document-reviewer",
            "document-writer",
            "fit-recommendation",
            "manual-review-warnings",
            "positioning-and-evidence",
            "questions-to-ask",
            "vacancy-evaluation",
        ]
        assert [item["id"] for item in payload["bundles"]] == [
            "application-materials",
            "company-vacancy-decision-support",
            "evaluate-vacancy",
        ]
        assert payload["bundles_by_id"]["evaluate-vacancy"]["skills"] == [
            "vacancy-evaluation",
            "positioning-and-evidence",
        ]
        assert payload["bundles_by_id"]["application-materials"]["skills"] == [
            "vacancy-evaluation",
            "positioning-and-evidence",
            "document-writer",
            "document-reviewer",
        ]

        skill_ids = {item["id"] for item in payload["skills"]}
        for bundle in payload["bundles"]:
            assert set(bundle["skills"]).issubset(skill_ids)

        boundary_text = json.dumps(payload, ensure_ascii=False)
        assert "job_intel/recruiter_read_facade.py" in boundary_text
        assert "Do not read SQLite directly from the skill." in boundary_text
        assert "Do not call CRM service, reconciler, or repository write paths." in boundary_text
        assert "~/.hermes/private/career/" in boundary_text
        assert "You must not send messages or apply to jobs." in boundary_text
        assert "draft only" in boundary_text.lower()

    def test_recruiter_context_is_json_serializable(self) -> None:
        payload = build_repo_role_package_skill_context(RECRUITER_DIR)
        encoded = json.dumps(payload, sort_keys=True)
        assert "hermes-recruiter" in encoded

    def test_global_skills_prompt_does_not_include_repo_local_recruiter_skills(self) -> None:
        prompt = build_skills_system_prompt()
        assert "vacancy-evaluation" not in prompt
        assert "positioning-and-evidence" not in prompt
        assert "document-writer" not in prompt
        assert "document-reviewer" not in prompt

    def test_generic_package_docs_are_discovered_without_recruiter_names(self, tmp_path: Path) -> None:
        package_dir = _write_role_package(
            tmp_path,
            docs={
                "alpha.md": "Alpha doc.",
                "zeta.md": "Zeta doc.",
            },
        )

        payload = build_repo_role_package_skill_context(package_dir, repo_root=tmp_path)

        assert [doc["path"] for doc in payload["package_docs"]] == [
            "custom-role/docs/alpha.md",
            "custom-role/docs/zeta.md",
        ]
        assert [doc["content"] for doc in payload["package_docs"]] == [
            "Alpha doc.",
            "Zeta doc.",
        ]

    def test_package_without_docs_dir_returns_empty_docs(self, tmp_path: Path) -> None:
        package_dir = _write_role_package(tmp_path, docs=None)

        payload = build_repo_role_package_skill_context(package_dir, repo_root=tmp_path)

        assert payload["package_docs"] == []

    def test_frontmatter_parser_unavailable_raises_controlled_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        package_dir = _write_role_package(tmp_path, docs=None)

        def _boom(_text: str):
            raise ImportError("agent.skill_utils unavailable")

        monkeypatch.setattr("hermes_cli.role_packages._parse_frontmatter_with_fallback", _boom)

        with pytest.raises(RolePackageError, match="frontmatter parser unavailable"):
            build_repo_role_package_skill_context(package_dir, repo_root=tmp_path)
