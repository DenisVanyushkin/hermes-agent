#!/usr/bin/env python3
"""6b T8: add hermes-home gateway blackbox target + alert rule, validate, reload.
Additive + reversible. Backs up, edits, promtool-checks, reloads, verifies green.
Reverts on any validation/reload failure."""
import subprocess, sys, shutil, time, urllib.request, urllib.parse, json

PROM = "/srv/services/prometheus/etc/prometheus.yml"
RULES = "/srv/services/prometheus/etc/alert_rules.yml"
STAMP = "bak-6b-20260714"
TARGET_LINE = '          - "http://192.168.20.10:80"\n'
ANCHOR = '          - "https://webhook.vanyushk.in"\n'
RULE = """  - alert: HermesHomeGatewayDown
    expr: probe_success{job="blackbox-http", instance="http://192.168.20.10:80"} == 0
    for: 3m
    labels: {severity: critical, source: prometheus}
    annotations:
      summary: "Гермес (Амина) gateway недоступен"
      description: "HTTP-проба к hermes-home gateway (192.168.20.10:80) не 2xx уже 3 минуты."

"""
RULE_ANCHOR = "- name: storage\n"

def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)

def container():
    r = sh("docker", "ps", "--filter", "name=prometheus_prometheus", "-q")
    return r.stdout.strip().splitlines()[0]

def main():
    # backups
    shutil.copy2(PROM, f"{PROM}.{STAMP}")
    shutil.copy2(RULES, f"{RULES}.{STAMP}")

    prom = open(PROM, encoding="utf-8").read()
    rules = open(RULES, encoding="utf-8").read()

    if TARGET_LINE.strip() in prom:
        print("target already present; skipping prom edit")
    else:
        if prom.count(ANCHOR) != 1:
            print(f"ABORT: anchor not unique in prometheus.yml ({prom.count(ANCHOR)})"); sys.exit(2)
        prom = prom.replace(ANCHOR, ANCHOR + TARGET_LINE, 1)

    if "HermesHomeGatewayDown" in rules:
        print("rule already present; skipping rules edit")
    else:
        if rules.count(RULE_ANCHOR) != 1:
            print(f"ABORT: rule anchor not unique ({rules.count(RULE_ANCHOR)})"); sys.exit(2)
        rules = rules.replace(RULE_ANCHOR, RULE + RULE_ANCHOR, 1)

    open(PROM, "w", encoding="utf-8").write(prom)
    open(RULES, "w", encoding="utf-8").write(rules)
    print("files edited")

    cid = container()
    c1 = sh("docker", "exec", cid, "promtool", "check", "config", "/etc/prometheus/prometheus.yml")
    print("promtool config:", c1.returncode, (c1.stdout + c1.stderr)[-600:])
    # 'check config' validates rule_files transitively, so c1 is authoritative.
    if c1.returncode != 0:
        print("VALIDATION FAILED -> reverting")
        shutil.copy2(f"{PROM}.{STAMP}", PROM)
        shutil.copy2(f"{RULES}.{STAMP}", RULES)
        sys.exit(3)

    # reload: /-/reload needs --web.enable-lifecycle; when that flag is off
    # prometheus answers 403 while curl still exits 0, so check the HTTP status
    # and fall back to SIGHUP (reloads config unconditionally). Revert only if
    # both fail. (2026-07-14: the old rc-only check reported false success.)
    r = sh("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", "http://localhost:9090/-/reload")
    code = (r.stdout or "").strip()
    print("reload http:", r.returncode, code, r.stderr[-200:])
    reloaded = (r.returncode == 0 and code.startswith("2"))
    if not reloaded:
        print(f"/-/reload not accepted (http={code}); falling back to SIGHUP")
        h = sh("docker", "kill", "--signal=HUP", cid)
        print("SIGHUP rc:", h.returncode, h.stderr[-200:])
        reloaded = (h.returncode == 0)
    if not reloaded:
        print("RELOAD FAILED -> reverting"); shutil.copy2(f"{PROM}.{STAMP}", PROM); shutil.copy2(f"{RULES}.{STAMP}", RULES)
        sys.exit(4)

    # verify probe green (allow up to ~90s for first scrape at 60s interval)
    q = 'probe_success{instance="http://192.168.20.10:80"}'
    url = "http://localhost:9090/api/v1/query?query=" + urllib.parse.quote(q)
    val = None
    for _ in range(10):
        time.sleep(10)
        try:
            data = json.load(urllib.request.urlopen(url, timeout=5))
            res = data.get("data", {}).get("result", [])
            if res:
                val = res[0]["value"][1]; break
        except Exception as e:
            print("query retry:", e)
    print("probe_success value:", val)
    if val == "1":
        print("SUCCESS: probe green")
    elif val is None:
        print("WARN: no probe_success sample yet (scrape may need more time); target added, check /rules manually")
    else:
        print(f"WARN: probe_success={val} (expected 1); target reachable earlier via curl — investigate")

if __name__ == "__main__":
    main()
