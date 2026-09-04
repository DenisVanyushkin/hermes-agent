"""Source-declared count against collected unique ids, over frozen corpora only."""
import pathlib, re, json, sys, collections

ROOTS = {
    "gate-a-20260829 (frozen)": pathlib.Path("/home/hermes/.hermes/job_intel/experiments/gate-a/0740165165a517fad40ea9ac443da094e178d06a/logs"),
    "d4-20260903": pathlib.Path("/home/hermes/.hermes/job_intel/experiments/gate-a/ce070a9e0f85bdfa2bcd1444bfe8e53cfaaffce1/logs"),
}
COUNT = re.compile(r'results-context-header__job-count">([^<]*)')
QUERY = re.compile(r'results-context-header__query-search">([^<]*)')
URN = re.compile(r"urn:li:jobPosting:(\d+)")

rows = []
for label, root in ROOTS.items():
    if not root.is_dir():
        continue
    for f in sorted(root.glob("*.html")):
        if "auth-validate" in f.name:
            continue
        html = f.read_text(errors="replace")
        c = COUNT.search(html)
        q = QUERY.search(html)
        ids = set(URN.findall(html))
        raw = c.group(1).strip() if c else None
        rows.append({
            "corpus": label, "file": f.name, "raw": raw,
            "query": q.group(1).strip() if q else None,
            "uniq": len(ids),
        })

def parse(raw):
    if raw is None:
        return ("absent", None)
    t = raw.replace(",", "").replace(" ", "").strip()
    if t.endswith("+"):
        t = t[:-1]
        return ("lower_bound", int(t)) if t.isdigit() else ("unparseable", None)
    return ("exact", int(t)) if t.isdigit() else ("unparseable", None)

stats = collections.Counter()
mismatches = []
ambiguous = []
for r in rows:
    kind, n = parse(r["raw"])
    r["kind"], r["n"] = kind, n
    stats[kind] += 1
    if kind == "exact":
        if n == r["uniq"]:
            stats["exact_match"] += 1
        elif n > r["uniq"]:
            stats["exact_gap"] += 1
            if r["uniq"] != 60:
                mismatches.append(r)
        else:
            stats["exact_less_than_collected"] += 1
            mismatches.append(r)
        if n == 60:
            ambiguous.append(r)

print("снимков всего:", len(rows))
print("по видам счётчика:", dict(stats))
print()
print("=== ТОЧНОЕ СОВПАДЕНИЕ счётчика и собранного ===")
ex = sorted({(r["n"], r["uniq"], r["query"]) for r in rows if r["kind"] == "exact" and r["n"] == r["uniq"]})
for n, u, q in ex:
    print(f"  {n:>5} = {u:<5} {q}")
print()
print("=== СЧЁТЧИК БОЛЬШЕ собранного (усечение) ===")
gp = sorted({(r["n"], r["uniq"], r["query"]) for r in rows if r["kind"] == "exact" and r["n"] > r["uniq"]})
for n, u, q in gp:
    print(f"  {n:>5} > {u:<5} {q}")
print()
print("=== КОНТРОЛЬ: собрано НЕ 60 при усечении (должно быть пусто) ===")
print([f"{r['n']}>{r['uniq']} {r['query']}" for r in mismatches] or "пусто")
print()
print("=== НЕОДНОЗНАЧНЫЕ: точное N ровно 60 ===")
print([f"{r['query']} uniq={r['uniq']}" for r in ambiguous] or "нет ни одного")
