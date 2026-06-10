"""
Dual-path routing trigger parity tests (Slice 2A/2B).

Validates that the YAML routing trigger data model
(config/hermes-routing-triggers.yaml) exactly matches the authoritative Python
constants in hermes_cli/profile_routing.py.

Python constants remain authoritative until Slice 2C. These tests are the
contract that ensures YAML and constants stay in sync during migration.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _REPO_ROOT / "config" / "hermes-profiles.yaml"
_POLICY_PATH = _REPO_ROOT / "config" / "hermes-model-policy.yaml"
_TRIGGERS_PATH = _REPO_ROOT / "config" / "hermes-routing-triggers.yaml"

from hermes_cli.profile_routing import (
    _ROUTING_DOMAINS,
    _DOCS_FIRST_MARKERS,
    _CONSTANTS_BY_DOMAIN,
    get_builtin_routing_terms_from_constants,
    get_builtin_routing_terms_from_yaml,
    load_routing_triggers,
    route_task,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def constants_terms():
    return get_builtin_routing_terms_from_constants()


@pytest.fixture(scope="module")
def yaml_terms():
    return get_builtin_routing_terms_from_yaml(_TRIGGERS_PATH)


@pytest.fixture(scope="module")
def triggers_raw():
    return load_routing_triggers(_TRIGGERS_PATH)


# ---------------------------------------------------------------------------
# 1. YAML data exists for all routing domains
# ---------------------------------------------------------------------------

def test_yaml_triggers_file_exists():
    assert _TRIGGERS_PATH.exists(), f"hermes-routing-triggers.yaml not found at {_TRIGGERS_PATH}"


def test_yaml_triggers_schema_version(triggers_raw):
    assert triggers_raw.get("schema_version") == 1


def test_yaml_domains_all_present(triggers_raw):
    domains = triggers_raw.get("domains", {})
    for domain in _ROUTING_DOMAINS:
        assert domain in domains, f"domain {domain!r} missing from hermes-routing-triggers.yaml"


def test_yaml_docs_first_markers_present(triggers_raw):
    assert "docs_first_markers" in triggers_raw, (
        "'docs_first_markers' section missing from hermes-routing-triggers.yaml"
    )


@pytest.mark.parametrize("domain", list(_ROUTING_DOMAINS))
def test_yaml_domain_has_triggers_key(triggers_raw, domain):
    entry = triggers_raw["domains"][domain]
    assert "triggers" in entry, f"domain {domain!r} is missing 'triggers' key"
    triggers = entry["triggers"]
    assert "en" in triggers, f"domain {domain!r} triggers missing 'en' key"
    assert "ru" in triggers, f"domain {domain!r} triggers missing 'ru' key"


# ---------------------------------------------------------------------------
# 2. YAML terms exactly match Python constants after normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", list(_ROUTING_DOMAINS))
def test_yaml_matches_constants_per_domain(constants_terms, yaml_terms, domain):
    c_set = set(constants_terms[domain])
    y_set = set(yaml_terms[domain])
    missing_from_yaml = c_set - y_set
    extra_in_yaml = y_set - c_set
    assert not missing_from_yaml, (
        f"[{domain}] terms in Python constants but missing from YAML: {sorted(missing_from_yaml)}"
    )
    assert not extra_in_yaml, (
        f"[{domain}] extra terms in YAML not present in Python constants: {sorted(extra_in_yaml)}"
    )


# ---------------------------------------------------------------------------
# 3. Docs-first markers in YAML exactly match Python constant
# ---------------------------------------------------------------------------

def test_docs_first_markers_parity(constants_terms, yaml_terms):
    c_set = set(constants_terms["docs_first_markers"])
    y_set = set(yaml_terms["docs_first_markers"])
    missing = c_set - y_set
    extra = y_set - c_set
    assert not missing, f"docs_first_markers in constants but missing from YAML: {sorted(missing)}"
    assert not extra, f"extra docs_first_markers in YAML not in constants: {sorted(extra)}"


def test_docs_first_markers_module_constant_matches_determine_logic():
    # Verify _DOCS_FIRST_MARKERS is the same set as what _determine_primary_profile uses.
    # We test this indirectly: route a prompt that contains a docs-first marker + infra trigger.
    # If docs-first wins, the constant is wired correctly.
    decision = route_task(
        "final status of the docker deployment",
        registry_path=_REGISTRY_PATH,
        policy_path=_POLICY_PATH,
    )
    assert decision.primary_profile == "scribe", (
        "'final status' is a docs-first marker and should beat 'docker' (infra); "
        f"got primary_profile={decision.primary_profile!r}"
    )


# ---------------------------------------------------------------------------
# 4. No duplicate triggers within a domain/language pair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", list(_ROUTING_DOMAINS))
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_no_duplicate_triggers_in_yaml(triggers_raw, domain, lang):
    triggers = triggers_raw["domains"][domain].get("triggers", {})
    terms = list(triggers.get(lang) or [])
    counts = Counter(terms)
    dupes = {t: n for t, n in counts.items() if n > 1}
    assert not dupes, f"[{domain}/{lang}] duplicate triggers: {dupes}"


@pytest.mark.parametrize("domain", list(_ROUTING_DOMAINS))
def test_no_duplicate_triggers_in_constants(domain):
    terms = list(_CONSTANTS_BY_DOMAIN[domain])
    counts = Counter(terms)
    dupes = {t: n for t, n in counts.items() if n > 1}
    assert not dupes, f"[{domain}] duplicate triggers in Python constants: {dupes}"


# ---------------------------------------------------------------------------
# 5. No trigger accidentally assigned to multiple built-in domains
# ---------------------------------------------------------------------------

def test_no_cross_domain_term_collisions(yaml_terms):
    # Collect all terms per domain and check for unintended multi-domain membership.
    # Collisions ARE allowed for terms that semantically belong to multiple domains
    # (there are currently none by design), so any collision is flagged.
    all_terms: dict[str, list[str]] = {d: yaml_terms[d] for d in _ROUTING_DOMAINS}
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for domain, terms in all_terms.items():
        for term in terms:
            if term in seen:
                collisions.append(f"'{term}' in both '{seen[term]}' and '{domain}'")
            else:
                seen[term] = domain
    assert not collisions, "Cross-domain term collisions:\n" + "\n".join(collisions)


# ---------------------------------------------------------------------------
# 6. Golden routing corpus still passes (smoke — full suite is in test_routing_golden_corpus.py)
# ---------------------------------------------------------------------------

def test_route_task_still_uses_python_constants():
    # route_task() must still work and produce consistent results.
    # Full golden corpus is verified in test_routing_golden_corpus.py.
    decision = route_task(
        "deploy docker service to production host",
        registry_path=_REGISTRY_PATH,
        policy_path=_POLICY_PATH,
    )
    assert decision.primary_profile == "engineer"
    assert decision.validation_status == "passed"


# ---------------------------------------------------------------------------
# 7. route_task() uses the real YAML routing triggers (Slice 2C)
# ---------------------------------------------------------------------------

def test_route_task_uses_yaml_routing_triggers(monkeypatch):
    # Slice 2C: route_task() now sources triggers from the real YAML. The real
    # YAML contains 'deploy' as an infra trigger, so this must still route to
    # 'engineer'. (Fallback / invalid-YAML behavior tested in test_profile_routing.py.)
    import hermes_cli.profile_routing as _mod
    _mod._clear_routing_terms_cache()
    try:
        decision = route_task(
            "deploy docker to production",
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        assert decision.primary_profile == "engineer", (
            "real YAML has 'deploy' as infra trigger; route_task() should return 'engineer'"
        )
    finally:
        _mod._clear_routing_terms_cache()


# ---------------------------------------------------------------------------
# 8. Profile architecture validator still passes
# ---------------------------------------------------------------------------

def test_profile_architecture_validator_passes():
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "validate_profile_architecture.py")],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"validate_profile_architecture.py failed:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 9. YAML policy section shape (Slice 2D)
# ---------------------------------------------------------------------------

def test_yaml_policy_section_exists(triggers_raw):
    assert "policy" in triggers_raw, "'policy' section missing from hermes-routing-triggers.yaml"


def test_yaml_policy_primary_domain_order_present(triggers_raw):
    policy = triggers_raw.get("policy", {})
    assert "primary_domain_order" in policy, "policy.primary_domain_order missing"


def test_yaml_policy_primary_domain_order_contains_all_domains(triggers_raw):
    policy = triggers_raw.get("policy", {})
    order = policy.get("primary_domain_order", [])
    for domain in _ROUTING_DOMAINS:
        assert domain in order, f"domain {domain!r} missing from policy.primary_domain_order"


def test_yaml_policy_primary_domain_order_no_duplicates(triggers_raw):
    policy = triggers_raw.get("policy", {})
    order = list(policy.get("primary_domain_order", []))
    counts = Counter(order)
    dupes = {d: n for d, n in counts.items() if n > 1}
    assert not dupes, f"policy.primary_domain_order has duplicates: {dupes}"


def test_yaml_policy_docs_first_enabled(triggers_raw):
    policy = triggers_raw.get("policy", {})
    docs_first = policy.get("docs_first", {})
    assert docs_first.get("enabled") is True, "policy.docs_first.enabled must be true"


def test_yaml_policy_overlays_enabled(triggers_raw):
    policy = triggers_raw.get("policy", {})
    overlays = policy.get("overlays", {})
    assert overlays.get("enabled") is True, "policy.overlays.enabled must be true"


def test_yaml_policy_max_chain_length(triggers_raw):
    policy = triggers_raw.get("policy", {})
    overlays = policy.get("overlays", {})
    assert overlays.get("max_chain_length") == 3, (
        f"policy.overlays.max_chain_length must be 3, got {overlays.get('max_chain_length')!r}"
    )


def test_yaml_policy_overlay_rules_present(triggers_raw):
    policy = triggers_raw.get("policy", {})
    overlays = policy.get("overlays", {})
    rules = overlays.get("rules", [])
    assert len(rules) >= 1, "policy.overlays.rules must not be empty"


def test_yaml_policy_overlay_rules_reference_valid_domains(triggers_raw):
    policy = triggers_raw.get("policy", {})
    overlays = policy.get("overlays", {})
    valid_domains = set(_ROUTING_DOMAINS)
    for rule in overlays.get("rules", []):
        for domain in rule.get("add_if_any_domain_matches", []):
            assert domain in valid_domains, (
                f"overlay rule references unknown domain {domain!r} in add_if_any_domain_matches"
            )


def test_yaml_policy_overlay_rules_reference_valid_profiles(triggers_raw):
    policy = triggers_raw.get("policy", {})
    overlays = policy.get("overlays", {})
    # Known routable profile IDs from the architecture.
    known_profiles = {
        "engineer", "security_auditor", "career_strategist", "scribe",
        "researcher", "general_operator", "chief_hermes",
    }
    for rule in overlays.get("rules", []):
        wp = rule.get("when_primary")
        assert wp in known_profiles, f"overlay rule when_primary={wp!r} is not a known profile"
        ap = rule.get("add_profile")
        assert ap in known_profiles, f"overlay rule add_profile={ap!r} is not a known profile"


# ---------------------------------------------------------------------------
# 10. YAML policy is active at runtime (Slice 2D)
# ---------------------------------------------------------------------------

def test_route_task_uses_yaml_routing_policy(monkeypatch):
    """route_task() reads max_chain_length from YAML policy.

    Inject a temp YAML that is identical to the real one except max_chain_length=1,
    then assert that a 3-hop task is truncated to 1 hop.
    """
    import hermes_cli.profile_routing as _mod

    # Load real triggers and override max_chain_length.
    real_data = load_routing_triggers(_TRIGGERS_PATH)
    real_data["policy"]["overlays"]["max_chain_length"] = 1

    tmp_path = _TRIGGERS_PATH.parent.parent / "tests" / "fixtures" / "_tmp_policy_test_triggers.yaml"
    try:
        import yaml as _yaml
        tmp_path.write_text(_yaml.dump(real_data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", tmp_path)
        _mod._clear_routing_terms_cache()
        _mod._clear_routing_policy_cache()

        # "production WebUI exposure change" → engineer + security_auditor + scribe (3 hops without cap)
        decision = route_task(
            "production WebUI exposure change",
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        chain = [h.profile_id for h in decision.route_chain]
        assert len(chain) == 1, (
            f"max_chain_length=1 should truncate chain to 1 hop; got {chain}"
        )
        assert decision.max_chain_limit_applied is True
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
        _mod._clear_routing_terms_cache()
        _mod._clear_routing_policy_cache()
