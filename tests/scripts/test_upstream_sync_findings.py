from scripts.upstream_sync_findings import render


def test_soft_symbol_finding_prints_command_on_its_own_line():
    text = render([{"finding_id": "INV-abc123456789", "path": "mod.py", "kind": "lost_definition", "symbol": "gone"}])
    lines = text.splitlines()
    assert lines[0].startswith("- mod.py:")
    assert lines[1] == "ack INV-abc123456789"
    from hermes_cli.upstream_sync_reply import parse_upstream_sync_ack_reply
    assert parse_upstream_sync_ack_reply(lines[1]) == "INV-abc123456789"


def test_line_only_hard_finding_never_gets_ack_form():
    text = render([{"finding_id": "INV-hard", "path": "mod.py", "kind": "unparseable", "line": 3}])
    assert text.startswith("- mod.py")
    assert "ack " not in text
