"""Behavioral tests for the bounded idea-signal source pipeline."""

from __future__ import annotations

import importlib.util
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "idea_signal_collector.py"
SPEC = importlib.util.spec_from_file_location("idea_signal_collector", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collector)

NOW = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)

RSS_FRESH = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Fresh useful signal</title><link>https://example.test/fresh?utm_source=x</link><pubDate>Tue, 11 Aug 2026 09:00:00 +0000</pubDate><description>Verified signal.</description></item>
  <item><title>Old signal</title><link>https://example.test/old</link><pubDate>Mon, 27 Jul 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""

RSS_FRESH_DUPLICATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Fresh useful signal</title><link>https://example.test/fresh</link><pubDate>Tue, 11 Aug 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""

RSS_NO_DATES = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Undated signal</title><link>https://example.test/undated</link></item>
</channel></rss>"""

GITHUB_RELEASES = json.dumps([
    {
        "name": "Hermes Agent v0.20.0",
        "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v0.20.0",
        "published_at": "2026-08-11T10:00:00Z",
        "body": "Bounded signal collector improvements.",
    }
])


def active_source(source_id: str = "source_a", basket: str = "health") -> dict:
    return {
        "id": source_id,
        "title": source_id,
        "basket": basket,
        "channel": "rss",
        "feed_url": f"https://{source_id}.test/feed.xml",
        "authority": "official",
        "trust_tier": "A",
        "status": "active",
        "max_items_per_run": 2,
        "timeout_seconds": 10,
        "retry_count": 1,
        "requires_published_date": True,
    }


def test_validate_registry_rejects_active_source_without_endpoint():
    source = active_source()
    source.pop("feed_url")

    with pytest.raises(ValueError, match="feed_url"):
        collector.validate_registry({"sources": [source]})


def test_validate_registry_rejects_unknown_lifecycle_state():
    source = active_source()
    source["status"] = "whatever"

    with pytest.raises(ValueError, match="status"):
        collector.validate_registry({"sources": [source]})


def test_validate_registry_rejects_active_source_that_allows_undated_items():
    source = active_source()
    source["requires_published_date"] = False

    with pytest.raises(ValueError, match="requires_published_date"):
        collector.validate_registry({"sources": [source]})


def test_candidate_state_enters_probation_when_registry_is_promoted():
    source = active_source()
    source["status"] = "probation"
    state = {
        "source_a": {
            "effective_status": "candidate",
            "reviewed_status": "candidate",
            "runs": 8,
            "successful_runs": 8,
            "items_seen": 16,
            "valid_date_items": 16,
            "accepted_items": 8,
            "duplicate_items": 7,
            "recent_results": [True] * 8,
        }
    }

    brief, state, _events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state, seen={}
    )
    assert brief["signals"] == []
    assert state["source_a"]["effective_status"] == "probation"
    assert state["source_a"]["runs"] == 1
    assert state["source_a"]["accepted_items"] == 1

    second, second_state, _events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state, seen=brief["emitted_seen"]
    )
    assert second["signals"] == []
    assert second_state["source_a"]["accepted_items"] == 1


def test_fetch_headers_use_github_media_type_without_poisoning_feed_negotiation():
    assert collector.request_headers("https://api.github.com/repos/NousResearch/hermes-agent/releases")["Accept"] == "application/vnd.github+json"
    assert "application/atom+xml" in collector.request_headers("https://example.test/feed.xml")["Accept"]


def test_parse_rss_canonicalizes_tracking_url_and_requires_published_date():
    items = collector.parse_feed(RSS_FRESH, channel="rss")

    assert items[0]["canonical_url"] == "https://example.test/fresh"
    assert items[0]["published_at"] == "2026-08-11T09:00:00+00:00"
    assert collector.parse_feed(RSS_NO_DATES, channel="rss")[0]["published_at"] is None


def test_parse_json_release_feed_extracts_published_date_and_url():
    items = collector.parse_feed(GITHUB_RELEASES, channel="github_releases")

    assert items == [{
        "title": "Hermes Agent v0.20.0",
        "canonical_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v0.20.0",
        "published_at": "2026-08-11T10:00:00+00:00",
        "summary": "Bounded signal collector improvements.",
        "channel": "github_releases",
    }]


def test_parse_published_at_handles_cpsc_rss_date_format():
    assert collector.parse_published_at("August 06, 2026").isoformat() == "2026-08-06T00:00:00+00:00"


def test_collect_rejects_url_emitted_in_a_prior_run():
    seen = {"url:https://example.test/fresh": NOW.isoformat()}

    brief, _state, _events = collector.collect_sources(
        [active_source()], lambda _url, _timeout: RSS_FRESH, now=NOW, state={}, seen=seen
    )

    assert brief["signals"] == []
    assert {row["reason"] for row in brief["rejections"]} == {"seen_in_prior_run", "stale"}


def test_prior_run_duplicate_counts_toward_lifecycle_disqualification():
    seen = {"url:https://example.test/fresh": NOW.isoformat()}
    state = {
        "source_a": {
            "effective_status": "active",
            "runs": 9,
            "successful_runs": 9,
            "items_seen": 9,
            "valid_date_items": 9,
            "accepted_items": 2,
            "duplicate_items": 7,
            "recent_results": [True],
        }
    }

    _brief, updated, events = collector.collect_sources(
        [active_source()], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state, seen=seen
    )

    assert updated["source_a"]["duplicate_items"] == 8
    assert updated["source_a"]["effective_status"] == "suspended"
    assert events[-1]["event"] == "suspended"


def test_probation_duplicates_do_not_poison_durable_duplicate_ratio_or_suspend():
    source = active_source()
    source["status"] = "probation"
    seen = {"url:https://example.test/fresh": NOW.isoformat()}
    state = {
        "source_a": {
            "effective_status": "probation",
            "reviewed_status": "probation",
            "runs": 4,
            "successful_runs": 4,
            "items_seen": 9,
            "valid_date_items": 9,
            "accepted_items": 2,
            "duplicate_items": 0,
            "recent_results": [True] * 4,
        }
    }

    _brief, updated, events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state, seen=seen
    )

    assert updated["source_a"]["effective_status"] == "probation"
    assert updated["source_a"]["duplicate_items"] == 0
    assert not events


def test_collect_rejects_old_undated_and_duplicate_items():
    sources = [
        active_source("source_a", "health"),
        active_source("source_b", "health"),
    ]
    responses = {
        "https://source_a.test/feed.xml": RSS_FRESH,
        "https://source_b.test/feed.xml": RSS_FRESH_DUPLICATE,
    }

    brief, _state, _events = collector.collect_sources(
        sources, lambda url, _timeout: responses[url], now=NOW, state={}
    )

    assert brief["run_status"] == "degraded"
    assert [signal["title"] for signal in brief["signals"]] == ["Fresh useful signal"]
    reasons = {row["reason"] for row in brief["rejections"]}
    assert {"stale", "seen_in_prior_run"}.issubset(reasons)


def test_collect_reports_no_signals_when_all_sources_complete_without_fresh_items():
    brief, _state, _events = collector.collect_sources(
        [active_source()], lambda _url, _timeout: RSS_NO_DATES, now=NOW, state={}
    )

    assert brief["run_status"] == "no_signals"
    assert brief["signals"] == []
    assert brief["source_failures"] == []
    assert brief["rejections"] == [{"source_id": "source_a", "reason": "missing_published_date", "title": "Undated signal"}]


def test_collect_reports_degraded_when_one_source_fails_but_other_source_has_signal():
    good = active_source("good")
    broken = active_source("broken")

    def fetch(url: str, _timeout: int) -> str:
        if "broken" in url:
            raise TimeoutError("timed out")
        return RSS_FRESH

    brief, _state, _events = collector.collect_sources([good, broken], fetch, now=NOW, state={})

    assert brief["run_status"] == "degraded"
    assert len(brief["signals"]) == 1
    assert brief["source_failures"] == [{"source_id": "broken", "reason": "fetch_failed: TimeoutError"}]


def test_collect_reports_degraded_when_required_baskets_are_missing_even_if_sources_succeed():
    brief, _state, _events = collector.collect_sources(
        [active_source("health", "health_habits_energy"), active_source("code", "programming_automation_hermes")],
        lambda _url, _timeout: RSS_FRESH,
        now=NOW,
        state={},
    )

    assert brief["run_status"] == "degraded"
    assert set(brief["missing_baskets"]) == {
        "finance_purchases_risk", "learning_work_practices", "home_travel_organization",
        "programming_automation_hermes", "relationships_leisure_quality_of_life",
    }


def test_collect_rejects_future_dated_items():
    future = RSS_FRESH.replace("Tue, 11 Aug 2026", "Fri, 14 Aug 2026")

    brief, _state, _events = collector.collect_sources(
        [active_source()], lambda _url, _timeout: future, now=NOW, state={}
    )

    assert brief["signals"] == []
    assert {row["reason"] for row in brief["rejections"]} == {"future_dated", "stale"}


def test_collect_reports_no_signals_when_sources_partly_fail_without_usable_signals():
    good = active_source("good", "health_habits_energy")
    broken = active_source("broken", "finance_purchases_risk")

    def fetch(url: str, _timeout: int) -> str:
        if "broken" in url:
            raise TimeoutError("timed out")
        return RSS_NO_DATES

    brief, _state, _events = collector.collect_sources([good, broken], fetch, now=NOW, state={})

    assert brief["run_status"] == "no_signals"
    assert brief["signals"] == []
    assert brief["source_failures"] == [{"source_id": "broken", "reason": "fetch_failed: TimeoutError"}]


def test_candidate_only_collection_is_no_signals_not_failed():
    source = active_source()
    source["status"] = "candidate"
    source.pop("feed_url")
    source.pop("channel")

    brief, _state, _events = collector.collect_sources([source], now=NOW, state={})

    assert brief["run_status"] == "no_signals"
    assert brief["source_failures"] == []


def test_collect_reports_failed_when_no_source_completes():
    def fetch(_url: str, _timeout: int) -> str:
        raise OSError("network down")

    brief, _state, _events = collector.collect_sources(
        [active_source()], fetch, now=NOW, state={}
    )

    assert brief["run_status"] == "failed"
    assert brief["signals"] == []


def test_probation_source_is_observed_but_not_emitted():
    source = active_source()
    source["status"] = "probation"

    brief, state, _events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state={}
    )

    assert brief["run_status"] == "no_signals"
    assert brief["signals"] == []
    assert state["source_a"]["items_seen"] == 2
    assert state["source_a"]["accepted_items"] == 1


def test_promoted_probation_source_becomes_eligible_without_manual_registry_edit():
    source = active_source()
    source["status"] = "probation"
    state = {"source_a": {"effective_status": "active"}}

    brief, _state, _events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state
    )

    assert [signal["title"] for signal in brief["signals"]] == ["Fresh useful signal"]


def test_degraded_source_is_explicitly_lower_confidence_but_can_supply_signal():
    source = active_source()
    state = {"source_a": {"effective_status": "degraded"}}

    brief, _state, _events = collector.collect_sources(
        [source], lambda _url, _timeout: RSS_FRESH, now=NOW, state=state
    )

    assert brief["run_status"] == "degraded"
    assert brief["signals"][0]["source_status"] == "degraded"


def test_source_lifecycle_promotes_probation_and_suspends_three_failures():
    promotable = {
        "effective_status": "probation",
        "runs": 5,
        "successful_runs": 5,
        "valid_date_items": 9,
        "items_seen": 10,
        "accepted_items": 4,
        "recent_results": [True, True, True, True, True],
    }
    assert collector.next_source_status(promotable) == "active"

    broken = {"effective_status": "active", "recent_results": [True, False, False, False]}
    assert collector.next_source_status(broken) == "suspended"


def test_probation_still_suspends_for_failures_or_missing_dates_but_not_duplicates():
    assert collector.next_source_status({
        "effective_status": "probation", "recent_results": [False, False, False],
    }) == "suspended"
    assert collector.next_source_status({
        "effective_status": "probation", "items_seen": 10, "valid_date_items": 0,
    }) == "suspended"
    assert collector.next_source_status({
        "effective_status": "probation", "items_seen": 10, "valid_date_items": 10,
        "duplicate_items": 8,
    }) == "probation"


def test_source_lifecycle_degrades_after_a_transient_failure_and_recovers_after_three_successes():
    assert collector.next_source_status({"effective_status": "active", "recent_results": [True, False]}) == "degraded"
    assert collector.next_source_status({"effective_status": "degraded", "recent_results": [False, True, True, True]}) == "active"


def test_source_lifecycle_degraded_source_needs_three_consecutive_successes_to_recover():
    almost_recovered = {
        "effective_status": "degraded", "runs": 20, "successful_runs": 19,
        "items_seen": 20, "valid_date_items": 20, "accepted_items": 10,
        "recent_results": [False, True],
    }
    assert collector.next_source_status(almost_recovered) == "degraded"


def test_source_lifecycle_suspends_undated_or_duplicate_heavy_feed_after_ten_items():
    undated = {"effective_status": "active", "items_seen": 10, "valid_date_items": 0, "recent_results": [True]}
    assert collector.next_source_status(undated) == "suspended"

    duplicate_heavy = {
        "effective_status": "active", "items_seen": 10, "valid_date_items": 10,
        "duplicate_items": 8, "recent_results": [True],
    }
    assert collector.next_source_status(duplicate_heavy) == "suspended"


def test_write_brief_persists_current_output_and_auditable_events(tmp_path):
    brief = {"run_id": "idea-signals-test", "run_status": "ok", "signals": []}
    collector.persist_run(tmp_path, brief, {"source_a": {"runs": 1}}, [{"source_id": "source_a", "event": "active"}])

    assert json.loads((tmp_path / "idea_signal_brief.json").read_text(encoding="utf-8")) == brief
    assert json.loads((tmp_path / "idea_source_health.json").read_text(encoding="utf-8"))["source_a"]["runs"] == 1
    events = (tmp_path / "idea_source_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(events[0])["source_id"] == "source_a"


def test_persist_run_writes_handoff_files_atomically(tmp_path):
    brief = {"run_id": "atomic", "run_status": "ok", "signals": []}
    collector.persist_run(tmp_path, brief, {"source_a": {"runs": 1}}, [], seen={})

    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "idea_signal_brief.json").read_text(encoding="utf-8"))["run_id"] == "atomic"


def test_load_usable_brief_refuses_failed_and_stale_inputs(tmp_path):
    stale = {
        "run_id": "old", "run_status": "ok", "signals": [],
        "collected_at": "2026-08-11T00:00:00+00:00",
    }
    (tmp_path / "idea_signal_brief.json").write_text(json.dumps(stale), encoding="utf-8")
    assert collector.load_usable_brief(tmp_path, now=NOW) is None

    failed = {"run_id": "bad", "run_status": "failed", "signals": [], "collected_at": NOW.isoformat()}
    (tmp_path / "idea_signal_brief.json").write_text(json.dumps(failed), encoding="utf-8")
    assert collector.load_usable_brief(tmp_path, now=NOW) is None


def test_load_usable_brief_refuses_future_dated_brief(tmp_path):
    future = {
        "run_id": "future", "run_status": "ok", "signals": [],
        "collected_at": "2026-08-14T00:00:00+00:00",
    }
    (tmp_path / "idea_signal_brief.json").write_text(json.dumps(future), encoding="utf-8")

    assert collector.load_usable_brief(tmp_path, now=NOW) is None


def test_load_usable_brief_keeps_current_ok_or_degraded_brief_with_signal(tmp_path):
    brief = {
        "run_id": "new", "run_status": "degraded",
        "signals": [{"title": "usable", "url": "https://example.test/usable"}],
        "collected_at": NOW.isoformat(),
    }
    (tmp_path / "idea_signal_brief.json").write_text(json.dumps(brief), encoding="utf-8")

    assert collector.load_usable_brief(tmp_path, now=NOW) == brief


def test_load_usable_brief_refuses_empty_degraded_handoff(tmp_path):
    brief = {"run_id": "empty", "run_status": "degraded", "signals": [], "collected_at": NOW.isoformat()}
    (tmp_path / "idea_signal_brief.json").write_text(json.dumps(brief), encoding="utf-8")

    assert collector.load_usable_brief(tmp_path, now=NOW) is None


def test_fetch_url_rejects_response_larger_than_byte_ceiling_while_streaming(monkeypatch):
    class ChunkedResponse:
        def __init__(self):
            self.chunks = [b"a" * 3, b"b" * 3]
            self.status = 200
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return self.chunks.pop(0) if self.chunks else b""

    class PinnedConnection:
        def __init__(self, hostname, port, address, timeout):
            assert (hostname, port, address, timeout) == ("example.test", 443, "8.8.8.8", 7)

        def request(self, method, target, headers):
            assert (method, target) == ("GET", "/feed.xml")
            assert headers["User-Agent"] == collector.USER_AGENT

        def getresponse(self):
            return ChunkedResponse()

        def close(self):
            pass

    monkeypatch.setattr(collector, "MAX_RESPONSE_BYTES", 5)
    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    monkeypatch.setattr(collector, "_PinnedHTTPSConnection", PinnedConnection, raising=False)

    with pytest.raises(ValueError, match="response exceeds"):
        collector.fetch_url("https://example.test/feed.xml", 7)


@pytest.mark.parametrize("target", [
    "http://example.test/feed.xml",
    "https://127.0.0.1/feed.xml",
    "https://10.0.0.8/feed.xml",
    "https://169.254.2.3/feed.xml",
    "https://[::1]/feed.xml",
])
def test_redirect_destinations_must_remain_public_https(target, monkeypatch):
    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))])

    with pytest.raises(ValueError, match="unsafe redirect destination"):
        collector.resolve_https_target(target)


def test_fetch_url_pins_validated_addresses_across_redirects(monkeypatch):
    resolutions = {
        "origin.test": "8.8.8.8",
        "redirect.test": "1.1.1.1",
    }
    connections = []

    class Response:
        def __init__(self, status, headers=None, body=b""):
            self.status = status
            self.headers = headers or {}
            self.body = body

        def read(self, _size=-1):
            body, self.body = self.body, b""
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class PinnedConnection:
        def __init__(self, hostname, port, address, timeout):
            connections.append((hostname, port, address, timeout))
            self.hostname = hostname

        def request(self, _method, _target, headers):
            assert headers["User-Agent"] == collector.USER_AGENT

        def getresponse(self):
            if self.hostname == "origin.test":
                return Response(302, {"Location": "https://redirect.test/final"})
            return Response(200, body=b"pinned")

        def close(self):
            pass

    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda host, *_args, **_kwargs: [(None, None, None, None, (resolutions[host], 443))])
    monkeypatch.setattr(collector, "_PinnedHTTPSConnection", PinnedConnection, raising=False)

    assert collector.fetch_url("https://origin.test/start", 4) == "pinned"
    assert connections == [
        ("origin.test", 443, "8.8.8.8", 4),
        ("redirect.test", 443, "1.1.1.1", 4),
    ]


def test_fetch_url_rejects_unbounded_dns_results(monkeypatch):
    addresses = [(None, None, None, None, (f"8.8.8.{index}", 443)) for index in range(1, 10)]
    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)

    with pytest.raises(ValueError, match="too many resolved addresses"):
        collector.fetch_url("https://example.test/feed.xml", 3)


def test_dry_run_does_not_create_state_or_lock_artifacts(tmp_path, monkeypatch):
    registry = tmp_path / "sources.yaml"
    registry.write_text("sources: []\n", encoding="utf-8")
    state_dir = tmp_path / "never-created"

    monkeypatch.setattr(
        collector,
        "collect_sources",
        lambda *_args, **_kwargs: ({"run_id": "dry", "run_status": "no_signals", "signals": [], "emitted_seen": {}}, {}, []),
    )

    assert collector.run(registry, state_dir, dry_run=True)["run_id"] == "dry"
    assert not state_dir.exists()


def test_collector_lock_preserves_manual_suspension_written_while_collection_is_pending(tmp_path, monkeypatch):
    health_path = Path(__file__).resolve().parents[2] / "scripts" / "idea_source_health.py"
    health_spec = importlib.util.spec_from_file_location("idea_source_health_for_lock_test", health_path)
    health = importlib.util.module_from_spec(health_spec)
    assert health_spec and health_spec.loader
    health_spec.loader.exec_module(health)

    registry = tmp_path / "sources.yaml"
    registry.write_text("""sources:
  - id: source_a
    title: source_a
    basket: health
    channel: rss
    feed_url: https://source_a.test/feed.xml
    status: active
    requires_published_date: true
""", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "idea_source_health.json").write_text(json.dumps({"source_a": {"effective_status": "active"}}), encoding="utf-8")
    collector_loaded = threading.Event()
    allow_collector_persist = threading.Event()
    health_finished = threading.Event()

    def delayed_collect(_sources, *, state, seen, **_kwargs):
        collector_loaded.set()
        assert allow_collector_persist.wait(timeout=2)
        return {"run_id": "race", "run_status": "no_signals", "signals": [], "emitted_seen": seen}, state, []

    monkeypatch.setattr(collector, "collect_sources", delayed_collect)
    collector_thread = threading.Thread(target=collector.run, args=(registry, state_dir))
    collector_thread.start()
    assert collector_loaded.wait(timeout=2)

    def suspend():
        health.main(["--registry", str(registry), "--state-dir", str(state_dir), "suspend", "source_a", "--reason", "operator action"])
        health_finished.set()

    health_thread = threading.Thread(target=suspend)
    health_thread.start()
    assert not health_finished.wait(timeout=0.1)
    allow_collector_persist.set()
    collector_thread.join(timeout=2)
    health_thread.join(timeout=2)

    assert not collector_thread.is_alive()
    assert not health_thread.is_alive()
    persisted = json.loads((state_dir / "idea_source_health.json").read_text(encoding="utf-8"))
    assert persisted["source_a"]["effective_status"] == "suspended"


def test_checked_in_registry_is_valid():
    registry, sources = collector.load_registry(Path(__file__).resolve().parents[2] / "config" / "idea_sources.yaml")

    assert registry["min_usable_signals"] == 3
    assert len(sources) <= collector.MAX_SOURCES_PER_RUN
    assert {"probation", "candidate"}.issubset({source["status"] for source in sources})


def test_default_registry_prefers_sibling_runtime_copy_when_not_in_repo(tmp_path):
    runtime_scripts = tmp_path / "runtime" / "scripts"
    runtime_scripts.mkdir(parents=True)
    sibling_registry = runtime_scripts / "idea_sources.yaml"
    sibling_registry.write_text("sources: []\n", encoding="utf-8")

    assert collector.resolve_registry_path(runtime_scripts / "idea_signal_collector.py") == sibling_registry
