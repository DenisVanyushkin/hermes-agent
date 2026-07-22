"""Tests for scripts/net_probe_dns.py — the resolver-delta probe."""
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "net_probe_dns.py"
spec = importlib.util.spec_from_file_location("net_probe_dns", MODULE_PATH)
net_probe_dns = importlib.util.module_from_spec(spec)
# Register in sys.modules before exec: on Python 3.12, @dataclass with
# `from __future__ import annotations` resolves forward-ref checks via
# sys.modules[cls.__module__], which crashes with AttributeError if the
# module executed by exec_module() was never registered there.
sys.modules["net_probe_dns"] = net_probe_dns
spec.loader.exec_module(net_probe_dns)


def test_compare_answers_disjoint_sets_are_a_mismatch():
    assert net_probe_dns.compare_answers({"1.2.3.4"}, {"5.6.7.8"}) is True


def test_compare_answers_overlapping_sets_are_not_a_mismatch():
    # CDNs legitimately return different subsets; any intersection means agreement.
    assert net_probe_dns.compare_answers({"1.2.3.4", "1.2.3.5"}, {"1.2.3.5"}) is False


def test_compare_answers_identical_sets_are_not_a_mismatch():
    assert net_probe_dns.compare_answers({"1.2.3.4"}, {"1.2.3.4"}) is False


def test_render_metrics_emits_seconds_and_success_for_each_resolver():
    results = [
        net_probe_dns.ProbeResult(
            name="example.org",
            system_ips={"1.2.3.4"}, system_seconds=0.01, system_ok=True,
            doh_ips={"1.2.3.4"}, doh_seconds=0.02, doh_ok=True,
        )
    ]
    out = net_probe_dns.render_metrics(results)
    assert 'hermes_dns_resolve_success{name="example.org",resolver="system"} 1' in out
    assert 'hermes_dns_resolve_success{name="example.org",resolver="doh"} 1' in out
    assert 'hermes_dns_resolve_seconds{name="example.org",resolver="system"} 0.01' in out
    assert 'hermes_dns_resolve_seconds{name="example.org",resolver="doh"} 0.02' in out
    assert 'hermes_dns_answer_mismatch{name="example.org"} 0' in out


def test_render_metrics_reports_mismatch_when_answers_are_disjoint():
    results = [
        net_probe_dns.ProbeResult(
            name="api.telegram.org",
            system_ips={"9.9.9.9"}, system_seconds=0.01, system_ok=True,
            doh_ips={"149.154.167.220"}, doh_seconds=0.03, doh_ok=True,
        )
    ]
    out = net_probe_dns.render_metrics(results)
    assert 'hermes_dns_answer_mismatch{name="api.telegram.org"} 1' in out


def test_render_metrics_omits_mismatch_entirely_when_doh_is_unreachable():
    """Absence of data is more honest than a false 'they agree'."""
    results = [
        net_probe_dns.ProbeResult(
            name="api.telegram.org",
            system_ips={"1.2.3.4"}, system_seconds=0.01, system_ok=True,
            doh_ips=set(), doh_seconds=0.0, doh_ok=False,
        )
    ]
    out = net_probe_dns.render_metrics(results)
    assert "hermes_dns_answer_mismatch" not in out
    assert 'hermes_dns_resolve_success{name="api.telegram.org",resolver="doh"} 0' in out


def test_render_metrics_omits_mismatch_when_system_resolver_fails():
    results = [
        net_probe_dns.ProbeResult(
            name="api.telegram.org",
            system_ips=set(), system_seconds=0.0, system_ok=False,
            doh_ips={"149.154.167.220"}, doh_seconds=0.02, doh_ok=True,
        )
    ]
    out = net_probe_dns.render_metrics(results)
    assert "hermes_dns_answer_mismatch" not in out


def test_render_metrics_includes_help_and_type_headers_once():
    results = [
        net_probe_dns.ProbeResult(
            name="a.example", system_ips={"1.1.1.1"}, system_seconds=0.01, system_ok=True,
            doh_ips={"1.1.1.1"}, doh_seconds=0.01, doh_ok=True,
        ),
        net_probe_dns.ProbeResult(
            name="b.example", system_ips={"2.2.2.2"}, system_seconds=0.01, system_ok=True,
            doh_ips={"2.2.2.2"}, doh_seconds=0.01, doh_ok=True,
        ),
    ]
    out = net_probe_dns.render_metrics(results)
    assert out.count("# TYPE hermes_dns_resolve_seconds gauge") == 1
    assert out.count("# TYPE hermes_dns_answer_mismatch gauge") == 1


def test_render_metrics_emits_probe_timestamp_using_provided_value():
    """render_metrics must stay pure: the timestamp is a parameter, not
    time.time() called internally, so output is deterministic under test."""
    results = [
        net_probe_dns.ProbeResult(
            name="example.org",
            system_ips={"1.2.3.4"}, system_seconds=0.01, system_ok=True,
            doh_ips={"1.2.3.4"}, doh_seconds=0.02, doh_ok=True,
        )
    ]
    out = net_probe_dns.render_metrics(results, now=1234567890.0)
    assert "hermes_dns_probe_timestamp_seconds 1234567890" in out
    assert out.count("# TYPE hermes_dns_probe_timestamp_seconds gauge") == 1


def test_render_metrics_probe_timestamp_defaults_without_now_argument():
    """Callers (and existing tests) that omit `now` still get a timestamp
    metric emitted — just not one asserted on for an exact value."""
    results = [
        net_probe_dns.ProbeResult(
            name="example.org",
            system_ips={"1.2.3.4"}, system_seconds=0.01, system_ok=True,
            doh_ips={"1.2.3.4"}, doh_seconds=0.02, doh_ok=True,
        )
    ]
    out = net_probe_dns.render_metrics(results)
    assert "hermes_dns_probe_timestamp_seconds" in out


def test_resolve_system_returns_ips_and_marks_success(monkeypatch):
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(net_probe_dns.socket, "getaddrinfo", fake_getaddrinfo)
    ips, seconds, ok = net_probe_dns.resolve_system("example.org")
    assert ips == {"93.184.216.34"}
    assert ok is True
    assert seconds >= 0.0


def test_resolve_system_marks_failure_on_gaierror(monkeypatch):
    def boom(*args, **kwargs):
        raise net_probe_dns.socket.gaierror("Temporary failure in name resolution")

    monkeypatch.setattr(net_probe_dns.socket, "getaddrinfo", boom)
    ips, seconds, ok = net_probe_dns.resolve_system("example.org")
    assert ips == set()
    assert ok is False


def test_resolve_doh_parses_json_answer():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"Answer": [
                {"type": 1, "data": "149.154.167.220"},
                {"type": 5, "data": "cname.example.org"},  # must be ignored
            ]}

    class FakeClient:
        def get(self, url, params=None, headers=None):
            return FakeResponse()

    ips, seconds, ok = net_probe_dns.resolve_doh("api.telegram.org", FakeClient())
    assert ips == {"149.154.167.220"}
    assert ok is True


def test_resolve_doh_marks_failure_when_request_raises():
    class FakeClient:
        def get(self, url, params=None, headers=None):
            raise RuntimeError("connection refused")

    ips, seconds, ok = net_probe_dns.resolve_doh("api.telegram.org", FakeClient())
    assert ips == set()
    assert ok is False


def test_write_atomically_leaves_no_temp_file(tmp_path):
    target = tmp_path / "net_probe_dns.prom"
    net_probe_dns.write_atomically(target, "hermes_dns_test 1\n")
    assert target.read_text() == "hermes_dns_test 1\n"
    # NOTE: deviates from the brief's `list(tmp_path.iterdir()) == [target]`.
    # This repo's tests/conftest.py autouse fixture unconditionally creates
    # tmp_path/hermes_test/ (HERMES_HOME sandbox) for every test, so that
    # exact-listing assertion can never hold here regardless of
    # write_atomically's correctness. Assert the intent instead: no stray
    # .tmp sibling from the write-then-rename.
    assert target.exists()
    assert not target.with_suffix(target.suffix + ".tmp").exists()
