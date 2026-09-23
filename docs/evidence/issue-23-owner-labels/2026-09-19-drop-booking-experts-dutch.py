import json, pathlib, shutil, datetime, os
os.umask(0o077)
p = pathlib.Path.home() / ".hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json"
b = p.with_suffix(".json.bak-before-strict-language-drop")
shutil.copy2(p, b); b.chmod(0o600)
data = json.loads(p.read_text())
for l in data["labels"]:
    if l["company"] == "Booking Experts B.V.":
        l.setdefault("verdict_previous", l["verdict"])
        l["verdict"] = "reject"
        l["why"] = "описание целиком на нидерландском; владелец 2026-09-19 выбрал строгий дроп не-en/ru, хотя сама роль ему нравилась"
        l["owner_ruling_2026_09_19_strict"] = "дропать строго: текст не на en/ru — выкидываем, даже если роль подходит"
data["label_history"].append({
  "changed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
  "change": "Booking Experts B.V. yes -> reject", "reason": "owner chose strict non-en/ru drop over keeping a liked role",
  "backup": b.name})
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); p.chmod(0o600)
print([(l["company"], l["verdict"], l.get("verdict_previous")) for l in data["labels"] if l.get("verdict_previous")])
