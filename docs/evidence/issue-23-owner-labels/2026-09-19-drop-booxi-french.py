import json, pathlib, shutil, datetime, os
os.umask(0o077)
p = pathlib.Path.home() / ".hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json"
backup = p.with_suffix(".json.bak-before-language-drop")
shutil.copy2(p, backup); backup.chmod(0o600)
data = json.loads(p.read_text())
changed = 0
for l in data["labels"]:
    if l["company"] == "Booxi":
        l["verdict_previous"] = l["verdict"]
        l["verdict"] = "reject"
        l["why"] = "текст вакансии на французском — владелец решил дропать (2026-09-19); прежний вердикт: годная, но заблокирована языком"
        l["owner_ruling_2026_09_19"] = "рабочие языки только en/ru; вакансию не на них дропаем сразу"
        changed += 1
data["label_history"] = data.get("label_history", []) + [{
  "changed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
  "change": "Booxi yes_blocked_language -> reject", "reason": "owner ruling: only en/ru are working languages",
  "backup": backup.name}]
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); p.chmod(0o600)
print("changed:", changed, "backup:", backup)
print(json.dumps([{k: v for k, v in l.items() if k in ("company","verdict","verdict_previous")} for l in data["labels"]], ensure_ascii=False)[:400])
