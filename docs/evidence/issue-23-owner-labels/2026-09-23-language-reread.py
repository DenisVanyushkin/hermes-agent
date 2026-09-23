"""One-off: owner re-read Mastercard and Kueski requirements (2026-09-23) - yes -> no."""
import datetime, json, os, pathlib, shutil
os.umask(0o077)
p = pathlib.Path.home() / ".hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json"
backup = p.with_name(p.name + ".bak-before-language-reread-2026-09-23")
shutil.copy2(p, backup); backup.chmod(0o600)
data = json.loads(p.read_text())
why = {
    "Mastercard": "требует мандарин (both Mandarin and English); владелец 2026-09-23: невнимательно прочёл требования, языковое требование оставляем",
    "Kueski": "требует испанский (fluency in both English and Spanish is required); владелец 2026-09-23: невнимательно прочёл требования, языковое требование оставляем",
}
changed = []
for l in data["labels"]:
    if l.get("round", "").startswith("2026-09-23") and l["company"] in why and l["verdict"] == "yes":
        l["verdict_previous"] = l["verdict"]; l["verdict"] = "no"; l["why"] = why[l["company"]]
        changed.append(l["company"])
assert sorted(changed) == ["Kueski", "Mastercard"], changed
data["label_history"].append({
    "changed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "change": "Mastercard, Kueski yes -> no",
    "reason": "owner re-read the requirements: a required non-en/ru language keeps the role out",
    "backup": backup.name})
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); p.chmod(0o600)
print("changed", changed)
