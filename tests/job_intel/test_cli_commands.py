from __future__ import annotations

from job_intel import cli


def test_cli_subcommands_include_new_hardening_commands() -> None:
    parser = cli.build_parser()
    subparser_action = next(action for action in parser._actions if getattr(action, "choices", None))
    assert {"doctor", "send-test", "retire-stale", "daily", "alert", "enrichment", "market", "strategic"}.issubset(set(subparser_action.choices))
