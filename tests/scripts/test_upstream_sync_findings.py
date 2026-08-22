from scripts.upstream_sync_findings import render


def test_soft_symbol_finding_prints_copyable_ack_at_line_start():
    text = render([{"finding_id": "INV-abc123456789", "path": "mod.py", "kind": "lost_definition", "symbol": "gone"}])
    assert text.startswith("ack INV-abc123456789")


def test_line_only_hard_finding_never_gets_ack_form():
    text = render([{"finding_id": "INV-hard", "path": "mod.py", "kind": "unparseable", "line": 3}])
    assert text.startswith("- mod.py")
    assert "ack " not in text
