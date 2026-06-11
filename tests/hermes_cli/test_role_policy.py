"""Tests for Slice 6: observe/warn role policy computation.

Tests are grouped into:
  1. Category map sync — KNOWN_TOOL_CATEGORIES must exactly match hermes-role-tool-map.yaml keys
  2. Policy evaluator — RolePolicyDecision output for each scenario
  3. Logging — observe_and_log emits the right structured record
  4. Dispatch safety — observe_and_log never raises; dispatch result unchanged
  5. Dispatch seam integration — dormant seam is safe with None manifest

No tool calls are blocked. No package roles become routable.
Dispatch integration is structurally present but live-dormant until active
package role context is wired.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_policy import (
    RolePolicyDecision,
    _get_tool_category,
    _load_tool_category_map,
    evaluate_role_tool_policy,
    observe_and_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(boundary_mode: str, tools: dict | None = None) -> dict[str, Any]:
    """Build a minimal manifest dict for policy evaluation tests."""
    role: dict[str, Any] = {
        "id": "test_role",
        "canonical_id": "test_role",
        "display_name": "Test Role",
    }
    if tools is not None:
        role["tools"] = tools
    return {
        "schema_version": 1,
        "package": {"name": "test-role", "version": "0.1.0"},
        "role": role,
        "boundary_mode": boundary_mode,
    }


_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOL_MAP_PATH = _REPO_ROOT / "config" / "hermes-role-tool-map.yaml"


# ---------------------------------------------------------------------------
# 1. Category map sync
# ---------------------------------------------------------------------------


class TestCategoryMapSync:
    """KNOWN_TOOL_CATEGORIES in role_packages.py must exactly match the
    category keys in config/hermes-role-tool-map.yaml.  This test is the
    machine-enforced contract between the two files."""

    def test_known_categories_match_yaml_keys(self):
        from hermes_cli.role_packages import KNOWN_TOOL_CATEGORIES

        assert _TOOL_MAP_PATH.exists(), (
            f"Tool category map not found at {_TOOL_MAP_PATH}"
        )
        raw = yaml.safe_load(_TOOL_MAP_PATH.read_text(encoding="utf-8"))
        yaml_keys = frozenset(raw.get("categories", {}).keys())

        assert yaml_keys == KNOWN_TOOL_CATEGORIES, (
            f"KNOWN_TOOL_CATEGORIES in role_packages.py does not match "
            f"config/hermes-role-tool-map.yaml keys.\n"
            f"  In YAML only: {yaml_keys - KNOWN_TOOL_CATEGORIES}\n"
            f"  In constant only: {KNOWN_TOOL_CATEGORIES - yaml_keys}\n"
            "Update the KNOWN_TOOL_CATEGORIES constant to match the YAML file."
        )

    def test_tool_names_used_in_tests_are_mapped(self):
        """Tool names used in evaluator tests must appear in the category map.

        This verifies that the test assertions ('file_search' -> read_only_inspection,
        'terminal' -> shell_general, 'deploy' -> production_deploy) are grounded in the
        actual mapping, not just assumed.
        """
        cat_map = _load_tool_category_map()
        required_mappings = {
            "file_search": "read_only_inspection",
            "terminal": "shell_general",
            "deploy": "production_deploy",
        }
        missing: list[str] = []
        wrong: list[str] = []
        for tool_name, expected_cat in required_mappings.items():
            actual = cat_map.get(tool_name)
            if actual is None:
                missing.append(
                    f"  '{tool_name}' not mapped (expected category: {expected_cat!r})"
                )
            elif actual != expected_cat:
                wrong.append(
                    f"  '{tool_name}' -> {actual!r}, expected {expected_cat!r}"
                )
        problems = missing + wrong
        assert not problems, (
            "Tool names used in test assertions are not correctly mapped in "
            f"config/hermes-role-tool-map.yaml:\n" + "\n".join(problems)
        )


# ---------------------------------------------------------------------------
# 2. Policy evaluator
# ---------------------------------------------------------------------------


class TestEvaluateRoleToolPolicy:

    def test_advisory_never_would_block(self):
        manifest = _manifest("advisory", tools={"denied_categories": ["production_deploy"]})
        decision = evaluate_role_tool_policy(manifest, "deploy", {})
        assert decision.would_block is False
        assert decision.enforced is False
        assert decision.boundary_mode == "advisory"

    def test_advisory_no_tools_no_block(self):
        manifest = _manifest("advisory")
        decision = evaluate_role_tool_policy(manifest, "terminal", {})
        assert decision.would_block is False

    def test_observe_warn_allowed_category_no_block(self):
        manifest = _manifest(
            "observe_warn",
            tools={"allowed_categories": ["read_only_inspection", "repo_edit"]},
        )
        decision = evaluate_role_tool_policy(manifest, "file_search", {})
        assert decision.would_block is False
        assert decision.allowed is True
        assert decision.category == "read_only_inspection"

    def test_observe_warn_denied_category_would_block(self):
        manifest = _manifest(
            "observe_warn",
            tools={"denied_categories": ["production_deploy"]},
        )
        decision = evaluate_role_tool_policy(manifest, "deploy", {})
        assert decision.would_block is True
        assert decision.enforced is False
        assert decision.boundary_mode == "observe_warn"
        assert any(
            "denied" in r.lower() or "production_deploy" in r
            for r in decision.reasons
        )

    def test_observe_warn_allowlist_miss_would_block(self):
        manifest = _manifest(
            "observe_warn",
            tools={"allowed_categories": ["read_only_inspection"]},
        )
        # terminal is in shell_general -- not in allowlist
        decision = evaluate_role_tool_policy(manifest, "terminal", {})
        assert decision.would_block is True
        assert decision.enforced is False

    def test_observe_warn_no_tools_no_block(self):
        manifest = _manifest("observe_warn")
        decision = evaluate_role_tool_policy(manifest, "terminal", {})
        assert decision.would_block is False

    def test_enforced_tools_computes_would_block_but_not_enforced(self):
        manifest = _manifest(
            "enforced_tools",
            tools={"denied_categories": ["production_deploy"]},
        )
        decision = evaluate_role_tool_policy(manifest, "deploy", {})
        assert decision.would_block is True
        assert decision.enforced is False, "enforced_tools must NOT enforce in Slice 6"

    def test_unknown_tool_name_no_block(self):
        manifest = _manifest(
            "observe_warn",
            tools={"denied_categories": ["production_deploy"]},
        )
        decision = evaluate_role_tool_policy(manifest, "completely_unknown_xyz", {})
        assert decision.would_block is False
        assert decision.category is None
        assert any("unknown" in r.lower() or "not mapped" in r.lower() for r in decision.reasons)

    def test_denied_wins_over_allowed_same_category(self):
        manifest = _manifest(
            "observe_warn",
            tools={
                "allowed_categories": ["read_only_inspection"],
                "denied_categories": ["read_only_inspection"],
            },
        )
        decision = evaluate_role_tool_policy(manifest, "file_search", {})
        assert decision.would_block is True, "denied must win over allowed for same category"

    def test_observe_warn_empty_allowlist_blocks_all(self):
        # allowed_categories: [] means nothing is permitted — all tools would_block
        manifest = _manifest(
            "observe_warn",
            tools={"allowed_categories": []},
        )
        decision = evaluate_role_tool_policy(manifest, "file_search", {})
        assert decision.would_block is True, (
            "Empty allowed_categories list should block all tools — not treat as no-policy"
        )

    def test_tool_category_lookup_returns_known(self):
        cat = _get_tool_category("file_search")
        assert cat == "read_only_inspection"

    def test_tool_category_lookup_returns_none_for_unknown(self):
        cat = _get_tool_category("absolutely_unknown_tool_xyz")
        assert cat is None

    def test_all_decisions_have_enforced_false(self):
        """Invariant: every RolePolicyDecision produced in Slice 6 has enforced=False."""
        test_cases = [
            ("advisory", {"denied_categories": ["production_deploy"]}, "deploy"),
            ("observe_warn", {"denied_categories": ["production_deploy"]}, "deploy"),
            ("observe_warn", {"allowed_categories": ["read_only_inspection"]}, "terminal"),
            ("enforced_tools", {"denied_categories": ["production_deploy"]}, "deploy"),
            ("observe_warn", None, "terminal"),
        ]
        for bm, tools, tool_name in test_cases:
            manifest = _manifest(bm, tools=tools)
            decision = evaluate_role_tool_policy(manifest, tool_name, {})
            assert decision.enforced is False, (
                f"enforced=True for boundary_mode={bm!r}, tool={tool_name!r} -- "
                "no blocking allowed in Slice 6"
            )


# ---------------------------------------------------------------------------
# 3. Logging
# ---------------------------------------------------------------------------


class TestObserveAndLogEmission:

    def test_would_block_event_emitted_for_denied_tool(self, caplog):
        manifest = _manifest(
            "observe_warn",
            tools={"denied_categories": ["production_deploy"]},
        )
        with caplog.at_level(logging.WARNING, logger="hermes_cli.role_policy"):
            observe_and_log(
                role_manifest=manifest,
                role_package="test-role",
                tool_name="deploy",
                tool_args={},
            )
        assert any("role_policy_would_block" in r.message for r in caplog.records), (
            f"Expected would_block log record; got: {[r.message for r in caplog.records]}"
        )

    def test_would_block_log_contains_required_fields(self, caplog):
        manifest = _manifest(
            "observe_warn",
            tools={"denied_categories": ["production_deploy"]},
        )
        with caplog.at_level(logging.WARNING, logger="hermes_cli.role_policy"):
            observe_and_log(
                role_manifest=manifest,
                role_package="test-role",
                tool_name="deploy",
                tool_args={},
            )
        record_msg = next(
            r.message for r in caplog.records if "role_policy_would_block" in r.message
        )
        for field in ("boundary_mode", "role_package", "tool_name", "enforced=false"):
            assert field in record_msg, f"Expected field {field!r} in log record: {record_msg!r}"

    def test_advisory_emits_no_would_block(self, caplog):
        manifest = _manifest("advisory", tools={"denied_categories": ["production_deploy"]})
        with caplog.at_level(logging.DEBUG, logger="hermes_cli.role_policy"):
            observe_and_log(
                role_manifest=manifest,
                role_package="test-role",
                tool_name="deploy",
                tool_args={},
            )
        assert not any("role_policy_would_block" in r.message for r in caplog.records)

    def test_observe_warn_allowed_tool_no_log(self, caplog):
        manifest = _manifest(
            "observe_warn",
            tools={"allowed_categories": ["read_only_inspection"]},
        )
        with caplog.at_level(logging.WARNING, logger="hermes_cli.role_policy"):
            observe_and_log(
                role_manifest=manifest,
                role_package="test-role",
                tool_name="file_search",
                tool_args={},
            )
        assert not any("role_policy_would_block" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Dispatch safety
# ---------------------------------------------------------------------------


class TestDispatchSafety:

    def test_observe_and_log_never_raises_on_bad_manifest(self):
        try:
            observe_and_log(
                role_manifest={"not_a_real": "manifest"},
                role_package="broken-role",
                tool_name="terminal",
                tool_args={},
            )
        except Exception as exc:
            pytest.fail(f"observe_and_log raised unexpectedly: {exc}")

    def test_observe_and_log_never_raises_on_none_manifest(self):
        try:
            observe_and_log(
                role_manifest=None,
                role_package="none-role",
                tool_name="terminal",
                tool_args={},
            )
        except Exception as exc:
            pytest.fail(f"observe_and_log raised unexpectedly: {exc}")

    def test_observe_and_log_always_returns_none(self):
        manifest = _manifest("observe_warn", tools={"denied_categories": ["production_deploy"]})
        result = observe_and_log(
            role_manifest=manifest,
            role_package="test-role",
            tool_name="deploy",
            tool_args={},
        )
        assert result is None, "observe_and_log must always return None -- it must not block"

    def test_observe_and_log_returns_none_on_allowed_tool(self):
        manifest = _manifest("observe_warn", tools={"allowed_categories": ["read_only_inspection"]})
        result = observe_and_log(
            role_manifest=manifest,
            role_package="test-role",
            tool_name="file_search",
            tool_args={},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 5. Dispatch seam integration
# ---------------------------------------------------------------------------


class TestDispatchSeamIntegration:
    """Dispatch integration is structurally present but live-dormant until
    active package role context is wired."""

    def test_none_manifest_is_noop(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="hermes_cli.role_policy"):
            observe_and_log(role_manifest=None, role_package="", tool_name="terminal", tool_args={})
        assert not any("role_policy_would_block" in r.message for r in caplog.records)

    def test_malformed_role_field_does_not_propagate(self):
        bad_manifest = {
            "boundary_mode": "observe_warn",
            "role": "not_a_dict",
            "package": {"name": "x", "version": "0.1.0"},
            "schema_version": 1,
        }
        try:
            observe_and_log(
                role_manifest=bad_manifest,
                role_package="bad",
                tool_name="terminal",
                tool_args={},
            )
        except Exception as exc:
            pytest.fail(f"Exception escaped observe_and_log: {exc}")

    def test_observe_and_log_import_does_not_raise(self):
        """The module must be importable from agent/tool_executor.py context."""
        try:
            from hermes_cli.role_policy import observe_and_log as _fn  # noqa: F401
        except ImportError as exc:
            pytest.fail(f"hermes_cli.role_policy import failed: {exc}")
