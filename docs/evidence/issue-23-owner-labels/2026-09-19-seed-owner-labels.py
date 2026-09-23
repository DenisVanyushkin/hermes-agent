import json, pathlib, sqlite3, os
os.umask(0o077)
labels = [
 ("Chief Product Officer", "Travix", "yes", "product org in a travel software platform"),
 ("Chief Product Officer (CPO)", "Booking Experts B.V.", "yes", "vertical SaaS product org"),
 ("Head of Banking Product Portfolio (m/f/d)", "SAP Fioneer", "no", "banking software portfolio domain"),
 ("Head of Brands Ads Product Strategy & Operations HQ", "deliveryhero", "no", "ads platform domain"),
 ("Head of Ads Platform Product & Operations", "Clapper", "no", "ads platform domain"),
 ("Portfolio Management, Product Lead (Vice President)", "OCBC", "no", "bank lending P&L"),
 ("Руководитель e-commerce / Head of E-commerce", "National Security&Communication", "no", "commercial e-commerce lead"),
 ("VP Product", "Unitary", "yes", "product org in a B2B AI software company"),
 ("Head of Product", "Xapien", "yes", "product org under CPTO — scope is not a criterion"),
 ("Product Director, Shine Advisor", "Shine", "yes", "single product area in a fintech SaaS"),
 ("Director of Product Management", "Usercentrics", "yes", "single pillar in a privacy SaaS"),
 ("Directeur Produit | Product Director", "Booxi", "yes_blocked_language", "required French"),
 ("Head of Product", "Tracksuit", "no", "12-month parental-leave cover"),
]
root = pathlib.Path.home() / ".hermes/job_intel/manual-shortlist/labels"
root.mkdir(parents=True, exist_ok=True); root.chmod(0o700)
c = sqlite3.connect("file:/var/lib/job-intel/state/job_intel.sqlite3?mode=ro", uri=True); c.execute("PRAGMA query_only=ON")
out = []
for title, company, verdict, why in labels:
    row = c.execute("""select vacancy_key,title,company,location,url,description from vacancies
        where title=? and company=? and description is not null order by id desc limit 1""", (title, company)).fetchone()
    if not row:
        out.append({"title": title, "company": company, "verdict": verdict, "why": why, "found_in_db": False}); continue
    out.append({"vacancy_key": row[0], "title": row[1], "company": row[2], "location": row[3],
                "url": row[4], "description_chars": len(row[5] or ""), "verdict": verdict, "why": why, "found_in_db": True})
p = root / "owner-labels-2026-09.json"
p.write_text(json.dumps({"labels": out, "rounds": ["2026-09-18-s2", "2026-09-19-round2"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
p.chmod(0o600)
print("written", p, "labels", len(out), "missing", sum(1 for x in out if not x["found_in_db"]))
