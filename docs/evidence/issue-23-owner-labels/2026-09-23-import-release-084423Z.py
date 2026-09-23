"""One-off: append owner decisions for release shortlist-20260922T084423Z to owner labels.

Input is a JSON export of the returned workbook (the host has no openpyxl).
Append-only: a vacancy_key already labelled keeps its first answer. The file is
backed up first; the batch carries its domain mix so a later reader cannot
mistake many fintech "yes" answers for a domain preference.
"""
import datetime, json, os, pathlib, shutil, sys

os.umask(0o077)
RELEASE = "shortlist-20260922T084423Z"
src = json.load(open(sys.argv[1]))
p = pathlib.Path.home() / ".hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json"
data = json.loads(p.read_text())
known = {l.get("vacancy_key") for l in data["labels"]}

backup = p.with_name(p.name + ".bak-before-" + RELEASE)
shutil.copy2(p, backup)
backup.chmod(0o600)

added, skipped = [], []
for r in src["rows"]:
    if r["vacancy_key"] in known:
        skipped.append(r["vacancy_key"])
        continue
    added.append({
        "vacancy_key": r["vacancy_key"], "title": r["title"], "company": r["company"],
        "location": r["location"], "url": r["url"], "verdict": r["owner_decision"],
        "why": r["owner_note"] or "", "round": "2026-09-23-" + RELEASE,
        "release_kind": r["kind"], "held_back_reason": r["held_back_reason"] or None,
        "domain": r["domain"], "domain_tagged_by": "claude-2026-09-23", "found_in_db": True,
    })
    known.add(r["vacancy_key"])

data["labels"].extend(added)
data["rounds"].append("2026-09-23-" + RELEASE)
data["label_history"].append({
    "changed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "change": f"release {RELEASE} decisions added ({len(added)}), skipped already labelled ({len(skipped)})",
    "reason": "owner returned the review workbook; projection_sha256 verified " + src["projection_sha256"][:12],
    "returned_file_sha256": src["returned_file_sha256"],
    "domain_mix": src["domain_mix"],
    "owner_caution": src["owner_caution"],
    "backup": backup.name,
})
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
p.chmod(0o600)
print(json.dumps({"added": len(added), "skipped": skipped, "total": len(data["labels"]), "backup": str(backup)}))
