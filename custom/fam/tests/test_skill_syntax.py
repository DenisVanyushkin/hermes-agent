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
