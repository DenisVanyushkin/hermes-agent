"""
Golden routing corpus test.

Loads the pre-generated golden_routing_corpus.yaml and asserts that the current
routing function produces the exact same output for every recorded prompt.

CORPUS UPDATES REQUIRE EXPLICIT GOLDEN APPROVAL:
  1. Review the routing change and confirm it is intentional.
  2. Run: python tests/fixtures/role_packages/generate_corpus.py
  3. Inspect the diff to golden_routing_corpus.yaml — every changed entry must be deliberate.
  4. Commit the updated corpus alongside the routing change in the same PR.

Do NOT regenerate the corpus during CI or inside test functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.profile_routing import route_task

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "role_packages" / "golden_routing_corpus.yaml"
_REGISTRY_PATH = _REPO_ROOT / "config" / "hermes-profiles.yaml"
_POLICY_PATH = _REPO_ROOT / "config" / "hermes-model-policy.yaml"


def _load_corpus() -> list[dict]:
    raw = _CORPUS_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return data["entries"]


_CORPUS_ENTRIES = _load_corpus()


def _route(prompt: str):
    return route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)


@pytest.mark.parametrize(
    "entry",
    _CORPUS_ENTRIES,
    ids=[e["id"] for e in _CORPUS_ENTRIES],
)
def test_golden_routing(entry: dict) -> None:
    """Fail loudly if routing behavior drifts from the locked golden corpus."""
    decision = _route(entry["prompt"])
    expected = entry["expected"]

    assert decision.primary_profile == expected["primary_profile"], (
        f"[{entry['id']}] primary_profile drift: "
        f"got {decision.primary_profile!r}, expected {expected['primary_profile']!r}\n"
        f"prompt: {entry['prompt']!r}"
    )
    got_chain = [h.profile_id for h in decision.route_chain]
    assert got_chain == expected["route_chain"], (
        f"[{entry['id']}] route_chain drift: "
        f"got {got_chain}, expected {expected['route_chain']}\n"
        f"prompt: {entry['prompt']!r}"
    )
    assert decision.confidence == expected["confidence"], (
        f"[{entry['id']}] confidence drift: "
        f"got {decision.confidence!r}, expected {expected['confidence']!r}"
    )
    assert decision.max_chain_limit_applied == expected["max_chain_limit_applied"], (
        f"[{entry['id']}] max_chain_limit_applied drift"
    )
    assert list(decision.ambiguity_reasons) == expected["ambiguity_reasons"], (
        f"[{entry['id']}] ambiguity_reasons drift:\n"
        f"  got:      {list(decision.ambiguity_reasons)}\n"
        f"  expected: {expected['ambiguity_reasons']}"
    )
