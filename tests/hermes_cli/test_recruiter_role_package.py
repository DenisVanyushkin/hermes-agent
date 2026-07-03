from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.role_packages import KNOWN_TOOL_CATEGORIES, validate_manifest_path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECRUITER_PKG = REPO_ROOT / "role-packages" / "recruiter"
REQUIRED_SKILLS = {
    "vacancy-evaluation",
    "positioning-and-evidence",
    "document-writer",
    "document-reviewer",
    "company-research",
    "company-assessment",
    "company-risk-register",
    "fit-recommendation",
    "questions-to-ask",
    "manual-review-warnings",
}
REQUIRED_BUNDLES = {
    "evaluate-vacancy.yaml": ["vacancy-evaluation", "positioning-and-evidence"],
    "application-materials.yaml": [
        "vacancy-evaluation",
        "positioning-and-evidence",
        "document-writer",
        "document-reviewer",
    ],
    "company-vacancy-decision-support.yaml": [
        "vacancy-evaluation",
        "company-research",
        "company-assessment",
        "company-risk-register",
        "fit-recommendation",
        "positioning-and-evidence",
        "questions-to-ask",
        "manual-review-warnings",
    ],
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestRecruiterRolePackageSkeleton:
    def test_manifest_validation_passes(self) -> None:
        manifest, errors, _warnings = validate_manifest_path(RECRUITER_PKG, check_builtin_collision=True)
        assert manifest is not None
        assert errors == []

    def test_required_paths_exist(self) -> None:
        assert RECRUITER_PKG.is_dir()
        assert (RECRUITER_PKG / "role-package.yaml").exists()
        assert (RECRUITER_PKG / "role.yaml").exists()
        assert (RECRUITER_PKG / "MANIFEST.md").exists()
        assert (RECRUITER_PKG / "docs" / "recruiter-role.md").exists()
        assert (RECRUITER_PKG / "docs" / "recruiter-boundaries.md").exists()

    def test_manifest_matches_recruiter_boundary_intent(self) -> None:
        data = _load_yaml(RECRUITER_PKG / "role-package.yaml")
        assert data["package"]["name"] == "hermes-recruiter"
        assert data["role"]["id"] == "hermes_recruiter"
        assert data["role"]["canonical_id"] == "hermes_recruiter"
        assert data["boundary_mode"] == "observe_warn"
        assert data.get("env_requires") == []
        categories = data["role"]["tools"]["allowed_categories"]
        assert set(categories).issubset(KNOWN_TOOL_CATEGORIES)
        assert "job_intel_read" in categories
        assert "read_only_inspection" in categories
        assert "repo_edit" not in categories
        assert "production_deploy" not in categories
        assert "secrets_read" not in categories

    def test_role_yaml_matches_manifest_identity(self) -> None:
        manifest = _load_yaml(RECRUITER_PKG / "role-package.yaml")
        role_yaml = _load_yaml(RECRUITER_PKG / "role.yaml")
        assert role_yaml["id"] == manifest["role"]["id"]
        assert role_yaml["canonical_id"] == manifest["role"]["canonical_id"]
        assert role_yaml["display_name"] == manifest["role"]["display_name"]

    def test_skill_files_exist_and_start_with_frontmatter(self) -> None:
        skills_dir = RECRUITER_PKG / "skills"
        found = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        assert found == REQUIRED_SKILLS
        for skill_name in REQUIRED_SKILLS:
            text = (skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
            assert text.startswith("---\n")
            assert "description:" in text
            assert "## Boundaries" in text
            assert "## Required Inputs" in text
            assert "## Expected Outputs" in text
            assert "## Failure Behavior" in text

    @pytest.mark.parametrize(
        ("skill_name", "required_phrases"),
        [
            (
                "vacancy-evaluation",
                [
                    "job_intel/recruiter_read_facade.py",
                    "job_intel/evaluator.py",
                    "job_intel/seed/scoring.yaml",
                    "must not create a parallel vacancy scoring system",
                    "must not send",
                ],
            ),
            (
                "positioning-and-evidence",
                [
                    "~/.hermes/private/career/",
                    "absence must not trigger invented facts",
                    "missing facts as gaps",
                ],
            ),
            (
                "document-writer",
                [
                    "POSITIONING_REQUIRED",
                    "draft only",
                    "must not generate vacancy-specific",
                    "claims require candidate facts or explicit Denis confirmation",
                ],
            ),
            (
                "document-reviewer",
                [
                    "draft only",
                    "unsupported claims",
                    "must not send",
                ],
            ),
        ],
    )
    def test_skill_boundary_phrases(self, skill_name: str, required_phrases: list[str]) -> None:
        text = (RECRUITER_PKG / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        for phrase in required_phrases:
            assert phrase in text

    def test_bundle_files_reference_existing_skills(self) -> None:
        bundles_dir = RECRUITER_PKG / "bundles"
        for filename, expected_skills in REQUIRED_BUNDLES.items():
            data = _load_yaml(bundles_dir / filename)
            assert data["skills"] == expected_skills
            for skill_name in expected_skills:
                assert (RECRUITER_PKG / "skills" / skill_name / "SKILL.md").exists()

    def test_manifest_and_docs_forbid_outbound_actions(self) -> None:
        joined = "\n".join(
            [
                (RECRUITER_PKG / "MANIFEST.md").read_text(encoding="utf-8"),
                (RECRUITER_PKG / "docs" / "recruiter-boundaries.md").read_text(encoding="utf-8"),
            ]
        )
        assert "draft only" in joined.lower()
        assert "must not send messages" in joined.lower() or "must not send" in joined.lower()
        assert "must not apply to jobs" in joined.lower() or "must not apply" in joined.lower()
