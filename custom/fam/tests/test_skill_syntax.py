import re
from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[2] / "skills/amina-fam/SKILL.md"


def test_event_command_templates_keep_subject_flags_in_sync():
    text = SKILL_PATH.read_text(encoding="utf-8")
    templates = []
    for match in re.finditer(r"`([^`]*fam cal (?:add|update)[^`]*)`", text):
        command = match.group(1)
        if ("fam cal add" in command and (
            "--title" in command or "--repeat weekly" in command
        )) or ("fam cal update" in command and "--start ISO" in command):
            line_number = text[:match.start()].count("\n") + 1
            kind = "update" if "fam cal update" in command else "add"
            templates.append((line_number, command, kind))

    assert len(templates) == 3
    missing = [
        (line_number, kind, command)
        for line_number, command, kind in templates
        if "--for-person" not in command
        or (kind == "update" and "--clear-for-person" not in command)
    ]
    assert not missing, f"subject flags missing from command templates: {missing}"


def test_event_command_templates_carry_remind_flags():
    """Schedule-only events (Taya's clubs) must be reachable from the
    canonical templates the agent copies, not only from the prose section:
    one-off add, recurring add and update each name the remind flag, and
    the series-level toggle has its own template row."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    missing = []
    for match in re.finditer(r"`([^`]*fam cal (?:add|update)[^`]*)`", text):
        command = match.group(1)
        if "fam cal add" in command and (
                "--title" in command or "--repeat weekly" in command):
            if "--no-remind" not in command:
                missing.append(command)
        elif "fam cal update" in command and "--start ISO" in command:
            if "--remind" not in command or "--no-remind" not in command:
                missing.append(command)
    assert not missing, f"remind flag missing from command templates: {missing}"
    assert "`fam cal series update <id> --no-remind`" in text


def test_series_reminder_stop_never_suggests_series_cancel():
    """Live 2026-09-22: «удали напоминание об актёрском мастерстве» ended in
    `cal series cancel`, wiping the lessons. The phrase table must map the
    series-wide stop to the remind toggle."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines()
               if line.startswith("| «не напоминай про <кружок/занятия>»"))
    assert "fam cal series update SERIES_ID --no-remind" in row
    assert "series cancel" not in row
