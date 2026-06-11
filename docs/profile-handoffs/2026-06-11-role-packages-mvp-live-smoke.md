# Role Packages MVP Live Smoke — 2026-06-11

## 1. Branch and HEAD

- **Branch**: `local/customizations`
- **HEAD**: `a16ddb5fe feat(roles): add observe-warn role policy`
- **MVP commits present**: a16ddb5fe, de8ee5dbf, 141b6d7d1, 58bf0013e, 2bebaa8ee, da07ee92e, f064669d1, 8c5ba47d4, 8b31fb084

Working tree had unrelated modifications in `job_intel/` — no role-package dirt.

---

## 2. Commands Run (Summary)

```
git status --short
git rev-parse --abbrev-ref HEAD
git log --oneline -7
python -m pytest tests/hermes_cli/test_routing_golden_corpus.py -q
python scripts/validate_profile_architecture.py
hermes role validate /tmp/smoke-role-pkg
hermes role install /tmp/smoke-role-pkg
hermes role list
hermes role info smoke-test-role
python -c "from agent.skill_utils import get_all_skills_dirs ..."
python -c "from hermes_cli.role_packages import cap_env_passthrough_for_skill ..."
hermes role validate /tmp/overlap-role-pkg
hermes role install /tmp/overlap-role-pkg   # blocked
hermes role remove smoke-test-role
hermes role list
python -m pytest tests/hermes_cli/test_routing_golden_corpus.py \
  tests/hermes_cli/test_role_package_cli.py \
  tests/hermes_cli/test_role_package_manifest.py \
  tests/hermes_cli/test_role_policy.py -q
python scripts/validate_profile_architecture.py
```

All `hermes role` commands run as `hermes` user (`sudo -u hermes`) with `.venv/bin/python`.

---

## 3. Temporary Package Names / Paths

| Package | Source Path | Install Path |
|---------|-------------|--------------|
| `smoke-test-role` | `/tmp/smoke-role-pkg` | `/home/hermes/.hermes/role-packages/smoke-test-role` |
| `overlap-test-role` (rejected) | `/tmp/overlap-role-pkg` | never installed |

Both `/tmp/` directories remain as cleanup artifacts (no sensitive content).

---

## 4. Install / List / Info / Validate / Remove Results

**Validate (smoke-test-role):**
```
valid: smoke-test-role v1.0.0  (role_id=smoke_test_role, boundary_mode=observe_warn)
```
Note: initial manifest draft was incorrect (`env_requires` as list of strings, not mappings; wrong trigger field path). Corrected during smoke.

**Install:**
```
installed: smoke-test-role v1.0.0  (role_id=smoke_test_role, boundary_mode=observe_warn)
  path: /home/hermes/.hermes/role-packages/smoke-test-role
Note: package roles are advisory only and not yet active in routing.
```

**List:**
```
NAME             VERSION  STATUS  ROLE_ID          BOUNDARY
smoke-test-role  1.0.0    active  smoke_test_role
```

**Info:**
```
  name          smoke-test-role
  version       1.0.0
  status        active
  source_type   local
  source_path   /tmp/smoke-role-pkg
  role_id       smoke_test_role
  install_path  /home/hermes/.hermes/role-packages/smoke-test-role
  installed_at  2026-06-11T05:15:11.443066+00:00
```

**Remove:**
```
removed: smoke-test-role
```
Post-remove list: `No role packages installed.`

---

## 5. Lockfile `accepted_env` Result

`lock.yaml` after install:
```yaml
packages:
  smoke-test-role:
    accepted_env: []
    ...
```

**Gap identified**: The `--accept-env` flag described in the smoke spec does not exist in the `hermes role install` CLI. The underlying `install_package()` function accepts an `accept_env` parameter but it is not wired to any CLI argument. As a result, `accepted_env` is always `[]` via the CLI.

To accept env vars, a caller must use `install_package(..., accept_env=["SAMPLE_FAKE_TOKEN"])` programmatically.

---

## 6. Package Skill Discovery Result

```python
from agent.skill_utils import get_all_skills_dirs
dirs = get_all_skills_dirs()
# → skill dirs: ['/home/hermes/.hermes/role-packages/smoke-test-role/skills']
# → package skill dir present: True
```

✅ Package skill directory included in skill discovery while installed.  
✅ Package skill directory absent after removal.

---

## 7. Env Passthrough Capping Result

Tested via `cap_env_passthrough_for_skill()` using the installed package at `/home/hermes/.hermes/role-packages/smoke-test-role`:

| Test case | Result |
|-----------|--------|
| `SAMPLE_FAKE_TOKEN` with `accepted_env=[]` (install default) | **blocked** (gate 2: not accepted) |
| `SAMPLE_FAKE_TOKEN` with `accepted_env=["SAMPLE_FAKE_TOKEN"]` (programmatic) | **allowed** |
| `UNDECLARED_FAKE_VAR` (not in manifest) | **blocked** (gate 1: not declared) |

All three gates function correctly. Actual env variable values were never read or printed.

---

## 8. Overlap Rejection Result

**Attempt 1** — first manifest used `role.routing_triggers` (wrong path): overlap not detected, install succeeded. Package removed.

**Attempt 2** — corrected manifest uses `role.routing.triggers` (correct path):

```
hermes role validate /tmp/overlap-role-pkg
  error: [EXACT_DUPLICATE] trigger 'deploy' (lang=en) is an exact duplicate of built-in 'engineer' trigger
  error: [ROUTING_FLIP] trigger 'deploy' matches golden corpus prompt (id='docs_first_en_final_status_over_infra') ...
  error: [ROUTING_FLIP] trigger 'deploy' matches golden corpus prompt (id='infra_en_deploy_webui') ...
  error: [ROUTING_FLIP] trigger 'deploy' matches golden corpus prompt (id='strip_cronjob_response_hot_infra_terms') ...

hermes role install /tmp/overlap-role-pkg
  error: manifest validation failed: [EXACT_DUPLICATE] ... [ROUTING_FLIP] ...
```

✅ Validate reports ERROR correctly.  
✅ Install blocked — no payload at `/home/hermes/.hermes/role-packages/overlap-test-role/`.

**Secondary gap identified**: `hermes role install` exits with code 0 even when validation fails and install is blocked. Should exit with code 1 for CI/scripting reliability.

---

## 9. Observe-Warn Evaluator Result

Test manifest: `boundary_mode=observe_warn`, `allowed_categories=[read_only_inspection]`.  
Tool under test: `deploy` (mapped to `production_deploy` category in `hermes-role-tool-map.yaml`).

```python
decision = evaluate_role_tool_policy(manifest, "deploy", {})
# decision.would_block = True
# decision.enforced = False
# decision.category = 'production_deploy'

result = observe_and_log(role_manifest=manifest, role_package="smoke-test-role",
                         tool_name="deploy", tool_args={})
# result = None
# No exception raised
```

Log output confirmed:
```
role_policy_would_block boundary_mode=observe_warn role_package=smoke-test-role
  role_id=smoke_test_role tool_name=deploy category=production_deploy
  reasons=["category 'production_deploy' is not in allowed_categories ['read_only_inspection']"]
  decision=would_block enforced=false
```

✅ `would_block=True`, `enforced=False`.  
✅ `observe_and_log` returns `None`, no exception.

Note: `production_deploy` category contains only `deploy` as an `[unconfirmed]` placeholder. Live dispatch observation remains limited until real tool names are mapped in `config/hermes-role-tool-map.yaml`.

---

## 10. Post-Smoke Tests

```
python -m pytest tests/hermes_cli/test_routing_golden_corpus.py \
  tests/hermes_cli/test_role_package_cli.py \
  tests/hermes_cli/test_role_package_manifest.py \
  tests/hermes_cli/test_role_policy.py -q

140 passed in 11.59s

python scripts/validate_profile_architecture.py
→ profile architecture validation passed
```

✅ Full regression suite passes. No regressions introduced.

---

## 11. Cleanup Confirmation

- `smoke-test-role` removed via `hermes role remove smoke-test-role`
- `lock.yaml` shows `packages: {}`
- `/home/hermes/.hermes/role-packages/smoke-test-role/` directory removed
- `overlap-test-role` was never installed (blocked by validation)
- `/tmp/smoke-role-pkg` and `/tmp/overlap-role-pkg` remain in `/tmp/` (no sensitive content, will be cleared by OS)

---

## 12. Gaps Found

| # | Severity | Description |
|---|----------|-------------|
| G1 | Medium | `--accept-env` flag not exposed in `hermes role install` CLI; `accepted_env` is always `[]` via CLI |
| G2 | Low | `hermes role install` exits with code 0 on validation failure (should be 1) |
| G3 | Info | `production_deploy` tool category contains only `[unconfirmed]` placeholder tool names; live tool blocking not yet possible |
| G4 | Info | `hermes role validate` does not check routing overlap by default (`check_overlap=False`); only `install` checks overlap |

---

## 13. GO / NO-GO Recommendation

**GO for MVP docs cleanup.**

All nine smoke scenarios passed their core assertions. The role package lifecycle (validate → install → list → info → remove) functions end-to-end. Env capping three-gate logic is correct. Overlap detection blocks installs at the `install` stage. The `observe_warn` evaluator correctly computes `would_block=True / enforced=False` without raising. All 140 unit tests pass post-smoke.

Gaps G1–G4 are known limitations within the MVP scope (advisory-only mode), not regressions. They warrant follow-up tasks but do not block MVP completion documentation.
