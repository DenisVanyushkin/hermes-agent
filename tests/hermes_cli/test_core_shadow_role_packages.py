"""Regression tests for core shadow role packages.

Five shadow packages mirroring the built-in Hermes roles:

  tests/fixtures/role_packages/core-shadow/hermes-scribe-core/
  tests/fixtures/role_packages/core-shadow/hermes-researcher-core/
  tests/fixtures/role_packages/core-shadow/hermes-engineer-core/
  tests/fixtures/role_packages/core-shadow/hermes-security-auditor-core/
  tests/fixtures/role_packages/core-shadow/hermes-career-strategist-core/

These tests validate structural correctness, no-shadow guarantees, and that
built-in routing is unaffected. They do NOT install packages into live
HERMES_HOME (tests are purely structural + validation API calls).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.role_packages import (
    KNOWN_TOOL_CATEGORIES,
    validate_manifest_path,
)
from hermes_cli.profile_validation import ACTIVE_PROFILE_IDS

REPO_ROOT = Path(__file__).resolve().parents[2]
SHADOW_DIR = REPO_ROOT / "tests" / "fixtures" / "role_packages" / "core-shadow"

ALL_SHADOW_PKGS = [
    "hermes-scribe-core",
    "hermes-researcher-core",
    "hermes-engineer-core",
    "hermes-security-auditor-core",
    "hermes-career-strategist-core",
]

# Built-in role IDs and package names that shadow packages must not use
BUILTIN_ROLE_IDS = set(ACTIVE_PROFILE_IDS)
BUILTIN_PACKAGE_NAMES = {
    "scribe", "researcher", "engineer", "security_auditor",
    "career_strategist", "general_operator", "chief_hermes",
}


@pytest.fixture(params=ALL_SHADOW_PKGS)
def shadow_pkg(request):
    return SHADOW_DIR / request.param


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestShadowManifestValidation:
    def test_full_validation_passes(self, shadow_pkg: Path) -> None:
        manifest, errors, _ = validate_manifest_path(shadow_pkg)
        assert errors == [], f"{shadow_pkg.name}: {errors}"
        assert manifest is not None

    def test_schema_only_validation_passes(self, shadow_pkg: Path) -> None:
        manifest, errors, _ = validate_manifest_path(
            shadow_pkg, check_builtin_collision=False
        )
        assert errors == [], f"{shadow_pkg.name} (schema-only): {errors}"

    def test_no_builtin_role_id_collision(self, shadow_pkg: Path) -> None:
        manifest, errors, _ = validate_manifest_path(
            shadow_pkg, check_builtin_collision=True
        )
        assert errors == [], f"{shadow_pkg.name}: builtin collision: {errors}"


# ---------------------------------------------------------------------------
# No-shadow guarantees
# ---------------------------------------------------------------------------


class TestShadowNoBulitin:
    """Ensures no shadow package uses a built-in role ID, canonical ID, or package name."""

    def test_role_id_is_not_builtin(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        role_id = data["role"]["id"]
        assert role_id not in BUILTIN_ROLE_IDS, (
            f"{shadow_pkg.name} role.id {role_id!r} collides with built-in"
        )

    def test_canonical_id_is_not_builtin(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        canonical_id = data["role"].get("canonical_id", data["role"]["id"])
        assert canonical_id not in BUILTIN_ROLE_IDS, (
            f"{shadow_pkg.name} role.canonical_id {canonical_id!r} collides with built-in"
        )

    def test_package_name_is_not_builtin(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        pkg_name = data["package"]["name"]
        assert pkg_name not in BUILTIN_PACKAGE_NAMES, (
            f"{shadow_pkg.name} package.name {pkg_name!r} collides with built-in"
        )

    def test_role_id_starts_with_hermes_prefix(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        role_id = data["role"]["id"]
        assert role_id.startswith("hermes_"), (
            f"{shadow_pkg.name} role.id {role_id!r} should start with hermes_"
        )

    def test_role_id_ends_with_core_suffix(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        role_id = data["role"]["id"]
        assert role_id.endswith("_core"), (
            f"{shadow_pkg.name} role.id {role_id!r} should end with _core"
        )


# ---------------------------------------------------------------------------
# Trigger uniqueness (no exact or obvious substring overlap with built-ins)
# ---------------------------------------------------------------------------


class TestShadowTriggerUniqueness:
    """Validates triggers by running the overlap-aware validate_manifest_path."""

    def test_no_overlap_errors_in_full_validation(self, shadow_pkg: Path) -> None:
        _, errors, _ = validate_manifest_path(shadow_pkg, check_builtin_collision=True)
        overlap_errors = [e for e in errors if "SUBSTRING" in e or "OVERLAP" in e or "overlap" in e]
        assert overlap_errors == [], f"{shadow_pkg.name} has trigger overlap: {overlap_errors}"

    def test_all_packages_have_en_triggers(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        en_triggers = data["role"]["routing"]["triggers"].get("en", [])
        assert en_triggers, f"{shadow_pkg.name} has no English triggers"

    def test_all_packages_have_ru_triggers(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        ru_triggers = data["role"]["routing"]["triggers"].get("ru", [])
        assert ru_triggers, f"{shadow_pkg.name} has no Russian triggers"


# ---------------------------------------------------------------------------
# Tool categories
# ---------------------------------------------------------------------------


class TestShadowToolCategories:
    def test_allowed_categories_are_known(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        cats = data.get("role", {}).get("tools", {}).get("allowed_categories", [])
        unknown = set(cats) - KNOWN_TOOL_CATEGORIES
        assert unknown == set(), f"{shadow_pkg.name}: unknown categories: {unknown}"

    def test_no_production_deploy_or_secrets_read(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        cats = data.get("role", {}).get("tools", {}).get("allowed_categories", [])
        forbidden = {"production_deploy", "secrets_read"}
        found = set(cats) & forbidden
        assert found == set(), f"{shadow_pkg.name}: forbidden categories present: {found}"


# ---------------------------------------------------------------------------
# Boundary mode
# ---------------------------------------------------------------------------


class TestShadowBoundaryMode:
    def test_all_use_observe_warn(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        assert data.get("boundary_mode") == "observe_warn", (
            f"{shadow_pkg.name} should use observe_warn at top level"
        )

    def test_no_enforced_tools(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        assert data.get("boundary_mode") != "enforced_tools", (
            f"{shadow_pkg.name} must not use enforced_tools in MVP"
        )


# ---------------------------------------------------------------------------
# Env requires
# ---------------------------------------------------------------------------


class TestShadowEnvRequires:
    def test_no_env_requires(self, shadow_pkg: Path) -> None:
        data = yaml.safe_load(
            (shadow_pkg / "role-package.yaml").read_text(encoding="utf-8")
        )
        assert data.get("env_requires") == [], (
            f"{shadow_pkg.name} should have empty env_requires"
        )


# ---------------------------------------------------------------------------
# Secret-shaped content
# ---------------------------------------------------------------------------


class TestShadowSecretClean:
    def test_no_secrets_in_any_file(self, shadow_pkg: Path) -> None:
        for f in shadow_pkg.rglob("*"):
            if f.is_file() and f.suffix in {".yaml", ".md", ".py", ".txt"}:
                text = f.read_text(encoding="utf-8", errors="replace").lower()
                for pattern in ("password=", "api_key=", "auth_token=", "secret="):
                    assert pattern not in text, (
                        f"Secret-shaped content {pattern!r} in {f}"
                    )

    def test_no_env_or_auth_json_files(self, shadow_pkg: Path) -> None:
        forbidden_names = {".env", "auth.json"}
        for f in shadow_pkg.rglob("*"):
            assert f.name not in forbidden_names, (
                f"Forbidden file {f.name} found in {shadow_pkg.name}"
            )


# ---------------------------------------------------------------------------
# MANIFEST.md presence
# ---------------------------------------------------------------------------


class TestShadowManifestMd:
    def test_all_have_manifest_md(self, shadow_pkg: Path) -> None:
        assert (shadow_pkg / "MANIFEST.md").exists(), (
            f"{shadow_pkg.name} is missing MANIFEST.md"
        )

    def test_manifest_md_mentions_builtin_source(self, shadow_pkg: Path) -> None:
        text = (shadow_pkg / "MANIFEST.md").read_text(encoding="utf-8")
        assert "built-in" in text.lower() or "built in" in text.lower(), (
            f"{shadow_pkg.name} MANIFEST.md should mention the source built-in role"
        )

    def test_manifest_md_mentions_mvp_limitations(self, shadow_pkg: Path) -> None:
        text = (shadow_pkg / "MANIFEST.md").read_text(encoding="utf-8")
        assert "mvp" in text.lower(), (
            f"{shadow_pkg.name} MANIFEST.md should mention MVP limitations"
        )


# ---------------------------------------------------------------------------
# Complete package set
# ---------------------------------------------------------------------------


class TestShadowCompleteness:
    def test_all_five_packages_exist(self) -> None:
        for pkg_name in ALL_SHADOW_PKGS:
            pkg_dir = SHADOW_DIR / pkg_name
            assert pkg_dir.is_dir(), f"Missing shadow package directory: {pkg_name}"
            assert (pkg_dir / "role-package.yaml").exists(), f"Missing manifest: {pkg_name}"
            assert (pkg_dir / "MANIFEST.md").exists(), f"Missing MANIFEST.md: {pkg_name}"

    def test_all_five_packages_have_distinct_role_ids(self) -> None:
        role_ids = []
        for pkg_name in ALL_SHADOW_PKGS:
            data = yaml.safe_load(
                (SHADOW_DIR / pkg_name / "role-package.yaml").read_text(encoding="utf-8")
            )
            role_ids.append(data["role"]["id"])
        assert len(role_ids) == len(set(role_ids)), f"Duplicate role IDs: {role_ids}"


# ---------------------------------------------------------------------------
# Golden routing corpus is unaffected
# ---------------------------------------------------------------------------


class TestGoldenCorpusUnaffected:
    """Runs the golden routing corpus to confirm built-in routing is unchanged."""

    def test_golden_routing_corpus_still_passes(self) -> None:
        """Run the golden routing corpus to confirm built-in routing is unchanged."""
        from hermes_cli.profile_routing import route_task
        import yaml as _yaml

        corpus_path = REPO_ROOT / "tests" / "fixtures" / "role_packages" / "golden_routing_corpus.yaml"
        if not corpus_path.exists():
            pytest.skip("golden_routing_corpus.yaml not found")

        corpus = _yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
        # Corpus uses "entries" key (not "cases")
        entries = corpus.get("entries", [])
        assert entries, "golden corpus has no entries"

        failures = []
        for entry in entries:
            prompt = entry.get("prompt", "")
            expected = entry.get("expected", {}).get("primary_profile", "")
            if not prompt or not expected:
                continue
            try:
                decision = route_task(prompt)
                actual = decision.primary_profile
            except Exception as exc:
                failures.append(f"{prompt!r}: exception {exc}")
                continue
            if actual != expected:
                failures.append(f"{prompt!r}: expected {expected!r}, got {actual!r}")

        assert not failures, "Golden corpus failures:\n" + "\n".join(failures)
