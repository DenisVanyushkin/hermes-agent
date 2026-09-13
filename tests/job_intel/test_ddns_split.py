import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _load_script():
    path = ROOT / "scripts/job_intel_ddns_watchdog.py"
    spec = importlib.util.spec_from_file_location("job_intel_ddns_watchdog_split", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolver_keeps_last_state_when_host_dns_fails(tmp_path: Path) -> None:
    watchdog = _load_script()
    state = tmp_path / "linkedin-ddns.json"
    previous = '{"endpoint":"213.211.78.39:3785","resolved_addresses":["213.211.78.39"]}\n'
    state.write_text(previous, encoding="utf-8")

    result = watchdog.resolve_and_store(
        config_text=(
            "[Peer]\nPublicKey = peer-key=\n"
            "Endpoint = router.example:3785\n"
        ),
        resolve=lambda _host: [],
        state_path=state,
        now="2026-09-13T17:00:00+00:00",
    )

    assert result.status == "resolution_failed"
    assert state.read_text(encoding="utf-8") == previous


def test_apply_uses_wg_directly_from_its_network_namespace() -> None:
    watchdog = _load_script()
    commands: list[list[str]] = []
    state = {
        "peer_key": "peer-key=",
        "endpoint_port": "3785",
        "resolved_addresses": ["213.211.78.39"],
    }

    result = watchdog.apply_endpoint(
        state_text=json.dumps(state),
        run=lambda command: commands.append(command),
        current_endpoint=lambda _interface, _peer: "95.56.123.201:3785",
        interface="wg0-ln",
    )

    assert result.status == "updated"
    assert commands == [
        [
            "wg",
            "set",
            "wg0-ln",
            "peer",
            "peer-key=",
            "endpoint",
            "213.211.78.39:3785",
        ]
    ]


def test_split_units_keep_dns_host_side_and_apply_capability_narrow() -> None:
    resolver = (ROOT / "deploy/systemd/job-intel-linkedin-ddns-resolver.service").read_text()
    apply_unit = (ROOT / "deploy/systemd/job-intel-linkedin-ddns-watchdog.service").read_text()

    assert "NetworkNamespacePath" not in resolver
    assert "ProtectHome=tmpfs\n" in resolver
    assert "NetworkNamespacePath=/run/netns/ln-eg\n" in apply_unit
    assert "CapabilityBoundingSet=CAP_NET_ADMIN\n" in apply_unit
    assert "AmbientCapabilities=CAP_NET_ADMIN\n" in apply_unit
    assert "CAP_SYS_ADMIN" not in apply_unit
    assert "ip netns exec" not in apply_unit
