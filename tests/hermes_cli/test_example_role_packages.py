"""Regression tests for the permanent example role packages.

tests/fixtures/role_packages/hermes-engineer-lab-example
tests/fixtures/role_packages/hermes-researcher-lab-example

These tests validate structural correctness only — they do not install
packages into a live HERMES_HOME so they remain fast and hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.role_packages import (
    KNOWN_TOOL_CATEGORIES,
    validate_manifest_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "tests" / "fixtures" / "role_packages"

ENGINEER_PKG = EXAMPLES_DIR / "hermes-engineer-lab-example"
RESEARCHER_PKG = EXAMPLES_DIR / "hermes-researcher-lab-example"


# ---------------------------------------------------------------------------
# Parameterised helpers
# ---------------------------------------------------------------------------

@pytest.fixture(params=["hermes-engineer-lab-example", "hermes-researcher-lab-example"])
def example_pkg(request):
    return EXAMPLES_DIR / request.param


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestExampleManifestValidation:
    def test_full_validation_passes(self, example_pkg: Path) -> None:
        manifest, errors, warnings = validate_manifest_path(example_pkg)
        assert errors == [], f"{example_pkg.name}: {errors}"
        assert manifest is not None

    def test_schema_only_validation_passes(self, example_pkg: Path) -> None:
        manifest, errors, warnings = validate_manifest_path(
            example_pkg, check_builtin_collision=False
        )
        assert errors == [], f"{example_pkg.name} (schema-only): {errors}"

    def test_no_builtin_role_id_collision(self, example_pkg: Path) -> None:
        manifest, errors, _ = validate_manifest_path(
            example_pkg, check_builtin_collision=True
        )
        assert errors == [], f"{example_pkg.name}: builtin collision: {errors}"

    def test_no_secret_shaped_content(self, example_pkg: Path) -> None:
        for f in example_pkg.rglob("*"):
            if f.is_file() and f.suffix in {".yaml", ".md", ".py", ".txt"}:
                text = f.read_text(encoding="utf-8", errors="replace")
                assert "sk-" not in text or "sk-" not in text.lower().replace("skill", "")
                lower = text.lower()
                for pattern in ("password=", "api_key=", "auth_token=", "secret="):
                    assert pattern not in lower, (
                        f"Secret-shaped content '{pattern}' in {f.relative_to(REPO_ROOT)}"
                    )


# ---------------------------------------------------------------------------
# Role ID and trigger uniqueness
# ---------------------------------------------------------------------------


class TestExampleRoleIds:
    def test_engineer_role_id_not_builtin_name(self) -> None:
        manifest_path = ENGINEER_PKG / "role-package.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        role_id = data["role"]["id"]
        assert role_id == "hermes_engineer_lab"
        assert role_id not in {"engineer", "researcher", "security_auditor", "scribe",
                               "career_strategist", "general_operator"}

    def test_researcher_role_id_not_builtin_name(self) -> None:
        manifest_path = RESEARCHER_PKG / "role-package.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        role_id = data["role"]["id"]
        assert role_id == "hermes_researcher_lab"
        assert role_id not in {"engineer", "researcher", "security_auditor", "scribe",
                               "career_strategist", "general_operator"}


# ---------------------------------------------------------------------------
# Tool categories
# ---------------------------------------------------------------------------


class TestExampleToolCategories:
    def _allowed_categories(self, pkg_dir: Path) -> list[str]:
        data = yaml.safe_load(
            (pkg_dir / "role-package.yaml").read_text(encoding="utf-8")
        )
        return data.get("role", {}).get("tools", {}).get("allowed_categories", [])

    def test_engineer_allowed_categories_are_known(self) -> None:
        cats = self._allowed_categories(ENGINEER_PKG)
        assert cats, "engineer-lab should declare allowed_categories"
        unknown = set(cats) - KNOWN_TOOL_CATEGORIES
        assert unknown == set(), f"Unknown categories: {unknown}"

    def test_researcher_allowed_categories_are_known(self) -> None:
        cats = self._allowed_categories(RESEARCHER_PKG)
        assert cats, "researcher-lab should declare allowed_categories"
        unknown = set(cats) - KNOWN_TOOL_CATEGORIES
        assert unknown == set(), f"Unknown categories: {unknown}"

    def test_researcher_read_only_only(self) -> None:
        cats = self._allowed_categories(RESEARCHER_PKG)
        assert cats == ["read_only_inspection"]


# ---------------------------------------------------------------------------
# Boundary mode
# ---------------------------------------------------------------------------


class TestExampleBoundaryModes:
    def test_both_use_observe_warn(self, example_pkg: Path) -> None:
        data = yaml.safe_load(
            (example_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        assert data.get("boundary_mode") == "observe_warn", (
            f"{example_pkg.name} should use observe_warn at top level"
        )


# ---------------------------------------------------------------------------
# Env requires
# ---------------------------------------------------------------------------


class TestExampleEnvRequires:
    def test_engineer_declares_sample_fake_token(self) -> None:
        data = yaml.safe_load(
            (ENGINEER_PKG / "role-package.yaml").read_text(encoding="utf-8")
        )
        env_names = [e["name"] for e in data.get("env_requires", [])]
        assert "SAMPLE_FAKE_TOKEN" in env_names

    def test_researcher_has_no_env_requires(self) -> None:
        data = yaml.safe_load(
            (RESEARCHER_PKG / "role-package.yaml").read_text(encoding="utf-8")
        )
        assert data.get("env_requires") == []


# ---------------------------------------------------------------------------
# Skill directory structure
# ---------------------------------------------------------------------------


class TestExampleSkillStructure:
    def test_engineer_skills_dir_exists(self) -> None:
        assert (ENGINEER_PKG / "skills").is_dir()

    def test_researcher_skills_dir_exists(self) -> None:
        assert (RESEARCHER_PKG / "skills").is_dir()

    def test_engineer_skill_has_skill_md(self) -> None:
        skill_dirs = list((ENGINEER_PKG / "skills").iterdir())
        assert skill_dirs, "engineer-lab should have at least one skill"
        for skill_dir in skill_dirs:
            assert (skill_dir / "SKILL.md").exists(), (
                f"Missing SKILL.md in {skill_dir.name}"
            )

    def test_researcher_skill_has_skill_md(self) -> None:
        skill_dirs = list((RESEARCHER_PKG / "skills").iterdir())
        assert skill_dirs, "researcher-lab should have at least one skill"
        for skill_dir in skill_dirs:
            assert (skill_dir / "SKILL.md").exists(), (
                f"Missing SKILL.md in {skill_dir.name}"
            )

    def test_engineer_has_expected_skill(self) -> None:
        assert (ENGINEER_PKG / "skills" / "engineering-implementation-brief").is_dir()

    def test_researcher_has_expected_skill(self) -> None:
        assert (RESEARCHER_PKG / "skills" / "research-brief-builder").is_dir()


# ---------------------------------------------------------------------------
# MANIFEST.md presence
# ---------------------------------------------------------------------------


class TestExampleManifestMd:
    def test_both_have_manifest_md(self, example_pkg: Path) -> None:
        assert (example_pkg / "MANIFEST.md").exists(), (
            f"{example_pkg.name} is missing MANIFEST.md"
        )
