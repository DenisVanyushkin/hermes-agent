# Core Role Shadow Migration
**Date:** 2026-06-11
**Branch:** local/customizations
**Status:** COMPLETE — GO

---

## 1. Branch and HEAD Before Work

- **Branch:** `local/customizations`
- **HEAD before:** `19acf38da` — test(roles): add engineer and researcher role package examples

---

## 2. Built-in Role Inventory Summary

Five built-in roles were inventoried from `config/hermes-profiles.yaml`,
`config/hermes-routing-triggers.yaml`, `hermes_cli/profile_routing.py`,
and `hermes_cli/profile_context.py`.

| Role | Domain | Model Tier | Key Tool Gaps for Packaging |
|---|---|---|---|
| `scribe` | docs (handoff, зафиксируй…) | standard | None — clean migration |
| `researcher` | research (btc, weather, digest…) | standard | web_search/browser not in package taxonomy |
| `engineer` | infra (deploy, docker, systemd…) | **reasoning** | shell_general advisory only; production approval gate not portable |
| `security_auditor` | security (auth, secrets, audit…) | **critical** | critical model tier not expressible; secrets_read intentionally excluded |
| `career_strategist` | career (vacancy, cv, резюме…) | standard | job_intel_read not in package taxonomy; email/slack categories absent |

Full inventory: `docs/profile-handoffs/2026-06-11-built-in-role-migration-inventory.md`

---

## 3. Packages Created

All five packages in `tests/fixtures/role_packages/core-shadow/`:

| Package | Role ID | Family | Boundary | Env |
|---|---|---|---|---|
| `hermes-scribe-core` | `hermes_scribe_core` | documentation | observe_warn | none |
| `hermes-researcher-core` | `hermes_researcher_core` | research | observe_warn | none |
| `hermes-engineer-core` | `hermes_engineer_core` | engineering | observe_warn | none |
| `hermes-security-auditor-core` | `hermes_security_auditor_core` | security | observe_warn | none |
| `hermes-career-strategist-core` | `hermes_career_strategist_core` | career | observe_warn | none |

Each package includes:
- `role-package.yaml` — complete manifest with persona, triggers, tool categories, boundary mode
- `MANIFEST.md` — describes source built-in, what was/was not migrated, MVP limitations, future path

**Trigger adjustment note:** Initial triggers for `hermes-security-auditor-core` and
`hermes-career-strategist-core` contained substrings of built-in terms (`security audit` ⊂ `audit`,
`career` ⊂ `career strategist core`). Triggers were adjusted to use abbreviations (`sa`, `cs`)
to eliminate overlap while remaining identifiable.

---

## 4. Files Added / Updated

### Added

```
tests/fixtures/role_packages/core-shadow/
├── hermes-career-strategist-core/
│   ├── role-package.yaml
│   └── MANIFEST.md
├── hermes-engineer-core/
│   ├── role-package.yaml
│   └── MANIFEST.md
├── hermes-researcher-core/
│   ├── role-package.yaml
│   └── MANIFEST.md
├── hermes-scribe-core/
│   ├── role-package.yaml
│   └── MANIFEST.md
└── hermes-security-auditor-core/
    ├── role-package.yaml
    └── MANIFEST.md

tests/hermes_cli/test_core_shadow_role_packages.py
docs/profile-handoffs/2026-06-11-built-in-role-migration-inventory.md
docs/profile-handoffs/2026-06-11-core-role-shadow-migration.md (this file)
```

### Updated

```
docs/role-packages/README.md           — added ## Core Shadow Packages section
docs/role-packages/authoring-guide.md  — added ## Core Shadow Packages section
docs/role-packages/operator-runbook.md — added ## Core Shadow Packages section
docs/profile-as-package/role-packages-backlog.md — added migration milestone
docs/profile-as-package/role-model-concept.md    — added migration strategy section
```

---

## 5. Validation Results

All five packages pass full and schema-only validation:

```
valid: hermes-career-strategist-core v0.1.0  (role_id=hermes_career_strategist_core, boundary_mode=observe_warn)
valid: hermes-engineer-core v0.1.0           (role_id=hermes_engineer_core, boundary_mode=observe_warn)
valid: hermes-researcher-core v0.1.0         (role_id=hermes_researcher_core, boundary_mode=observe_warn)
valid: hermes-scribe-core v0.1.0             (role_id=hermes_scribe_core, boundary_mode=observe_warn)
valid: hermes-security-auditor-core v0.1.0   (role_id=hermes_security_auditor_core, boundary_mode=observe_warn)
```

Warnings (expected, acceptable): `ROLE_FAMILY_OVERLAP` — shadow packages intentionally use the same
role families as their built-in counterparts. Documented in MANIFEST.md files.

No overlap errors. No secret-shaped content. No built-in ID collisions.

---

## 6. Test Results

All test suites pass:

```
test_core_shadow_role_packages.py     108 passed
test_example_role_packages.py          25 passed
test_role_package_manifest.py         (included in full suite)
test_role_package_cli.py              (included in full suite)
test_role_package_skills.py           (included in full suite)
test_role_policy.py                   (included in full suite)
  Subtotal (role package suite):      241 passed

test_routing_golden_corpus.py          53 passed
test_profile_routing.py               (included in full suite)
test_profile_validation.py            (included in full suite)
  Subtotal (routing suite):            98 passed

scripts/validate_profile_architecture.py: PASSED
```

**Total: 339 tests passed, 0 failed.**

---

## 7. Manual Smoke Results

All five packages were installed and removed on the live Hermes host:

**Install:**
```
installed: hermes-career-strategist-core v0.1.0  (role_id=hermes_career_strategist_core, boundary_mode=observe_warn)
installed: hermes-engineer-core v0.1.0           (role_id=hermes_engineer_core, boundary_mode=observe_warn)
installed: hermes-researcher-core v0.1.0         (role_id=hermes_researcher_core, boundary_mode=observe_warn)
installed: hermes-scribe-core v0.1.0             (role_id=hermes_scribe_core, boundary_mode=observe_warn)
installed: hermes-security-auditor-core v0.1.0   (role_id=hermes_security_auditor_core, boundary_mode=observe_warn)

Note: package roles are advisory only and not yet active in routing.
```

**List:** All five appeared as `active` with correct role IDs.

**Remove:** All five removed cleanly. Lockfile returned to `packages: {}`.

**Built-in routing after cleanup:** Golden corpus — 53 passed. Unchanged.

---

## 8. Known Limitations

| Limitation | Detail |
|---|---|
| Package routing not active | MVP design; shadow triggers are validated but not live |
| `critical` model tier | `security_auditor` uses `critical` in built-in; package manifest has `standard` |
| `reasoning` model tier | `engineer` uses `reasoning` in built-in; package manifest has `standard` |
| `web_search`/`browser` | Not in `KNOWN_TOOL_CATEGORIES`; researcher package uses `read_only_inspection` only |
| `job_intel_read` | Not in `KNOWN_TOOL_CATEGORIES`; career_strategist package uses `read_only_inspection` only |
| `shell_general` | Not declared in shadow packages; engineer package uses `repo_edit` only |
| `secrets_read` | Intentionally excluded from security_auditor shadow package |
| Approval gate portability | Production mutation approval gates are built-in policy; not portable to MVP packages |
| Overlay rules | Routing overlays (engineer+security_auditor, etc.) are built-in; not in package routing |
| Output style injection | Rendered by `profile_context.py`; not available to packages in MVP |

---

## 9. Recommendation for Next Migration Step

**Recommendation: GO — shadow packages are ready as migration fixtures.**

### Immediate next steps (before v1 routing activation):

1. **Expand `KNOWN_TOOL_CATEGORIES`** to include `web_search`, `browser`, `job_intel_read`, `shell_general`. This unblocks complete researcher, career_strategist, and engineer category declarations.

2. **Define model_tier_request for critical/reasoning** — add machinery so security_auditor can declare `critical` and engineer can declare `reasoning` in package manifests.

3. **observe_warn calibration run** — install the shadow packages and run for ≥1 week in production. Collect `[observe_warn] would_block=true` log events to identify false positives before enforcement.

4. **v1 routing activation** — once calibration shows acceptable false-positive rate, promote shadow triggers into the live routing layer.

### These shadow packages are suitable for:
- **Authoring guide examples** — real-world complexity vs. the minimal `hermes-*-lab` examples
- **CI regression tests** — 108 tests run on every push
- **Pre-release smoke testing** — install/remove lifecycle exercised
- **v1 routing activation readiness** — triggers already validated and unique

---
