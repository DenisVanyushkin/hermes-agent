"""Правки общих файлов под тулсет fitness_booking.

Патчем эти четыре файла не переносятся: репозитории хостов разошлись на тысячи
коммитов, и `git am --3way` падает на отсутствующих blob'ах пред-образа. Поэтому
правка якорная — по строке `legal_research`, которая есть в каждом из файлов ровно
один раз на любом из хостов.

Скрипт идемпотентен: повторный запуск ничего не дублирует.

Запускать из корня репозитория hermes-agent.
"""

import argparse
import pathlib
import re
import sys

ENTRY = "fitness_booking"
KEY_RE = re.compile(r"^ {2}[A-Za-z_][A-Za-z0-9_-]*:")

TOOLS = [
    "fitness_schedule",
    "fitness_my_bookings",
    "fitness_book",
    "fitness_cancel",
    "fitness_watch_add",
    "fitness_watch_list",
    "fitness_watch_remove",
]


def patch_toolsets(root: pathlib.Path) -> str:
    path = root / "toolsets.py"
    s = path.read_text(encoding="utf-8")
    if f'"{ENTRY}"' in s:
        return "уже есть"
    anchor = '    "legal_research": {'
    if anchor not in s:
        raise SystemExit("toolsets.py: не найден якорь legal_research")
    tools = ", ".join(f'"{t}"' for t in TOOLS)
    entry = (
        f'    "{ENTRY}": {{\n'
        '        "description": "Invictus: расписание групповых программ, запись, '
        'правила автозаписи",\n'
        f'        "tools": [{tools}],\n'
        '        "includes": []\n'
        '    },\n'
    )
    path.write_text(s.replace(anchor, entry + anchor, 1), encoding="utf-8")
    return "добавлено"


def patch_tools_config(root: pathlib.Path) -> str:
    path = root / "hermes_cli" / "tools_config.py"
    s = path.read_text(encoding="utf-8")
    if ENTRY in s:
        return "уже есть"
    anchor = '    ("legal_research",'
    idx = s.find(anchor)
    if idx == -1:
        raise SystemExit("tools_config.py: не найден якорь legal_research")
    end = s.index("\n", idx)
    line = (
        f'\n    ("{ENTRY}",  "\U0001f3cb️ Invictus Fitness", '
        '"schedule, book, cancel, autobook rules"),'
    )
    path.write_text(s[:end] + line + s[end:], encoding="utf-8")
    return "добавлено"


def patch_role_packages(root: pathlib.Path) -> str:
    path = root / "hermes_cli" / "role_packages.py"
    s = path.read_text(encoding="utf-8")
    if ENTRY in s:
        return "уже есть"
    anchor = '    "legal_research",\n})'
    if anchor not in s:
        raise SystemExit("role_packages.py: не найден якорь legal_research")
    replacement = f'    "legal_research",\n    "{ENTRY}",\n}})'
    path.write_text(s.replace(anchor, replacement, 1), encoding="utf-8")
    return "добавлено"


def patch_role_tool_map(root: pathlib.Path) -> str:
    path = root / "config" / "hermes-role-tool-map.yaml"
    s = path.read_text(encoding="utf-8")
    if f"{ENTRY}:" in s:
        return "уже есть"
    anchor = "  image_generation:\n"
    if anchor not in s:
        raise SystemExit("hermes-role-tool-map.yaml: не найден якорь image_generation")
    block = (
        f"  {ENTRY}:\n"
        "    description: >-\n"
        "      Invictus (entryx.io): расписание групповых программ, запись и отмена\n"
        "      записи, правила автозаписи. Отдельная категория, потому что запись и\n"
        "      отмена меняют внешнее состояние и стоят санкции клуба.\n"
        "    tools:\n" + "".join(f"      - {t}\n" for t in TOOLS)
    )
    path.write_text(s.replace(anchor, block + anchor, 1), encoding="utf-8")
    return "добавлено"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root", default=".", help="корень репозитория hermes-agent"
    )
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()
    if not (root / "toolsets.py").exists():
        print(f"❌ {root} не похож на корень hermes-agent", file=sys.stderr)
        return 1

    for name, fn in (
        ("toolsets.py", patch_toolsets),
        ("hermes_cli/tools_config.py", patch_tools_config),
        ("hermes_cli/role_packages.py", patch_role_packages),
        ("config/hermes-role-tool-map.yaml", patch_role_tool_map),
    ):
        print(f"{name}: {fn(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
