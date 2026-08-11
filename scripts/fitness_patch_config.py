"""Дописывает fitness_booking в platform_toolsets живого config.yaml.

Правка построчная, а не YAML round-trip: round-trip переупорядочил бы ключи по
алфавиту всему файлу. Комментарии не вносятся — ночной скрипт обновления
фолбэка отказывается переписывать конфиг с комментариями.

Платформы задаются флагом: на hermes-agent это cli+telegram, на hermes-home —
cli+whatsapp.

    python scripts/fitness_patch_config.py ~/.hermes/config.yaml \\
        ~/.hermes/config.yaml.pre-fitness --platforms cli,whatsapp
"""

import argparse
import pathlib
import re
import shutil
import sys

ENTRY = "fitness_booking"
# Ключ платформы: ровно два пробела отступа. Значение может стоять на той же
# строке ("discord: []") — такую строку тоже обязаны распознать, иначе вставка
# уедет за границу списка и сломает YAML.
KEY_RE = re.compile(r"^ {2}[A-Za-z_][A-Za-z0-9_-]*:")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("backup")
    ap.add_argument("--platforms", default="cli,telegram")
    args = ap.parse_args()

    targets = {p.strip() for p in args.platforms.split(",") if p.strip()}
    path = pathlib.Path(args.config)
    backup = pathlib.Path(args.backup)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    out: list[str] = []
    in_block = False
    current = None
    inserted: list[str] = []

    def flush():
        if current in targets and current not in inserted:
            out.append(f"  - {ENTRY}\n")
            inserted.append(current)

    for line in lines:
        stripped = line.rstrip("\n")
        if in_block:
            if stripped and not stripped.startswith(" "):
                flush()
                in_block = False
                current = None
            elif KEY_RE.match(stripped):
                flush()
                current = stripped.strip().split(":", 1)[0]
        if stripped == "platform_toolsets:":
            in_block = True
            current = None
        out.append(line)
    if in_block:
        flush()

    already = [t for t in targets if t not in inserted]
    if already:
        # Возможно, запись уже была сделана раньше — это не ошибка.
        text = path.read_text(encoding="utf-8")
        for t in already:
            print(f"⚠️  в {t} не вставлено (проверь, нет ли уже)", file=sys.stderr)
        if ENTRY not in text:
            return 1

    shutil.copy2(path, backup)
    path.write_text("".join(out), encoding="utf-8")

    # Проверяем результат и откатываемся сами: битый config.yaml — это лежачий
    # гейтвей, и обнаружить это на рестарте уже поздно.
    import yaml

    try:
        block = yaml.safe_load(path.read_text(encoding="utf-8"))["platform_toolsets"]
        for target in targets:
            assert ENTRY in block[target], f"{ENTRY} не попал в {target}"
    except Exception as exc:  # noqa: BLE001
        shutil.copy2(backup, path)
        print(f"❌ результат не проходит проверку ({exc}); конфиг откачен", file=sys.stderr)
        return 1

    print(f"бэкап: {backup}")
    print(f"вставлено в: {sorted(inserted)}")
    print("YAML валиден, платформы проверены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
