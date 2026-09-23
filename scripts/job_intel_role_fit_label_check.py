"""Compare role_fit verdicts against the owner labels, by vacancy_key."""
import json
import sqlite3
import sys

sys.path.insert(0, "/home/hermes/.hermes/hermes-agent")
from job_intel.product_search.role_fit import ROLE_FIT_RULESET_VERSION, evaluate_role_fit

labels = json.load(open("/home/hermes/.hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json"))["labels"]
conn = sqlite3.connect("file:/var/lib/job-intel/state/job_intel.sqlite3?mode=ro", uri=True)
conn.execute("PRAGMA query_only=ON")

print("ruleset:", ROLE_FIT_RULESET_VERSION, "labels:", len(labels))
rows = []
for label in labels:
    key = label["vacancy_key"]
    found = conn.execute(
        "SELECT title, company, location, description FROM vacancies WHERE vacancy_key = ? "
        "ORDER BY last_seen_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    if not found:
        rows.append((label, "missing_in_db", ()))
        continue
    title, company, location, description = (x or "" for x in found)
    decision = evaluate_role_fit(title, company, location, description)
    rows.append((label, decision.verdict, decision.rule_ids))

owner_yes = {"yes"}
agree = disagree_false_reject = disagree_false_accept = 0
print()
for label, verdict, rule_ids in rows:
    owner = label["verdict"]
    owner_positive = owner in owner_yes
    machine_positive = verdict == "accept"
    if verdict == "missing_in_db":
        mark = "??"
    elif owner_positive == machine_positive:
        mark = "OK"
        agree += 1
    elif owner_positive and not machine_positive:
        mark = "FALSE_REJECT"
        disagree_false_reject += 1
    else:
        mark = "FALSE_ACCEPT"
        disagree_false_accept += 1
    print(f"{mark:12} owner={owner:6} machine={verdict:9} {label['title'][:44]!r} @ {label['company'][:22]}")
    if mark in ("FALSE_REJECT", "FALSE_ACCEPT"):
        print(f"{'':12} rules={list(rule_ids)}")

print()
print(f"agree={agree} false_reject={disagree_false_reject} false_accept={disagree_false_accept}")
