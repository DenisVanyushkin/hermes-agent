# Role Packages Operator Runbook

This guide covers operational procedures for managing role packages in a running Hermes instance.

## Example Packages

Two reference packages live in `tests/fixtures/role_packages/`:

- **hermes-engineer-lab** — env consent demo; `observe_warn`; `read_only_inspection` + `repo_edit`
- **hermes-researcher-lab** — no-env demo; `observe_warn`; `read_only_inspection` only

Use them to smoke-test the role package CLI after a rebase or upgrade:

```bash
hermes role validate tests/fixtures/role_packages/hermes-engineer-lab-example
hermes role validate tests/fixtures/role_packages/hermes-researcher-lab-example
hermes role install tests/fixtures/role_packages/hermes-engineer-lab-example --accept-env SAMPLE_FAKE_TOKEN
hermes role install tests/fixtures/role_packages/hermes-researcher-lab-example
hermes role list
hermes role remove hermes-engineer-lab
hermes role remove hermes-researcher-lab
```

Both packages are regression-tested via `tests/hermes_cli/test_example_role_packages.py`.
Their routing triggers are not active in MVP.


## Core Shadow Packages

Five shadow packages mirror the current built-in Hermes roles under
`tests/fixtures/role_packages/core-shadow/`:

| Package | Shadow of | Role ID |
|---|---|---|
| `hermes-scribe-core` | `scribe` | `hermes_scribe_core` |
| `hermes-researcher-core` | `researcher` | `hermes_researcher_core` |
| `hermes-engineer-core` | `engineer` | `hermes_engineer_core` |
| `hermes-security-auditor-core` | `security_auditor` | `hermes_security_auditor_core` |
| `hermes-career-strategist-core` | `career_strategist` | `hermes_career_strategist_core` |

These are **not active replacements**. They exist as:

- **migration preparation fixtures** — installable, validated package-form versions of the built-in roles
- **authoring references** — examples of real-world manifests with complete personas, triggers, tool declarations, and MANIFEST.md docs
- **regression tests** — `tests/hermes_cli/test_core_shadow_role_packages.py` validates all five on every test run

**Package routing is not active.** Built-in domain-keyword routing (infra → engineer, security → security_auditor, etc.) remains authoritative. The shadow packages are installed and removed cleanly without affecting built-in routing.

**Boundary mode:** All five use `observe_warn` — the system logs what it would block but does not enforce.

**Future v1 migration path:** After observe_warn calibration, promote shadow triggers into the live routing layer. Enable `enforced_tools` after calibration shows near-zero false positives. Retire built-in profiles once package routing is proven stable.

## Quick Reference

| Task | Command |
|------|---------|
| Validate a package before install | `hermes role validate ./my-role-package` |
| Lightweight schema check | `hermes role validate ./my-role-package --schema-only` |
| Install with env access | `hermes role install ./my-role-package --accept-env MY_TOKEN` |
| List installed packages | `hermes role list` |
| View package details | `hermes role info my-role-package` |
| Remove a package | `hermes role remove my-role-package` |
| View lockfile | `cat ~/.hermes/role-packages/lock.yaml` |

## Installation Workflows

### Standard Installation

```bash
# Navigate to package directory
cd ~/path/to/my-role-package

# Validate first (optional but recommended)
hermes role validate .

# Install
hermes role install .

# Verify
hermes role list
hermes role info my-role-package
```

The installer will:

1. Parse and validate `role-package.yaml`
2. Check for trigger overlap with built-in roles
3. Scan for secret-shaped content
4. Prompt for environment variable consent (if any are declared)
5. Write package payload to `~/.hermes/role-packages/<name>/`
6. Write lockfile entry to `~/.hermes/role-packages/lock.yaml`
7. Clear the role registry and skill caches

### Installation with Pre-Agreed Consents

If the package declares environment variables and you want to grant them without prompting:

```bash
# Grant multiple env vars
hermes role install ./my-role-package \
  --accept-env API_TOKEN,DATABASE_URL,LOG_LEVEL

# Consent will be recorded in lock.yaml
cat ~/.hermes/role-packages/lock.yaml
```

### Installation from a Git Repository

```bash
# Clone the repo
git clone https://github.com/user/my-hermes-role.git
cd my-hermes-role

# Install
hermes role install .
```

### Installation with Schema-Only Validation

If you want to validate the manifest without triggering the full install flow:

```bash
hermes role validate ./my-role-package --schema-only

# Output will show schema errors only (if any)
```

## Inspection and Status Checking

### List All Installed Packages

```bash
hermes role list
```

Example output:

```
Name               Version   Status    Role ID          Display Name
my-advisor         0.1.0     active    my_advisor       My Advisor Role
legacy-role        1.0.0     active    legacy_role      Legacy Role
broken-pkg         0.1.0     broken    broken_pkg       Broken Package
```

Status codes:

- `active` — package is loaded and available
- `broken` — package failed validation; silently skipped at load time
- `disabled` — package exists but is disabled (future feature)

### Get Full Package Information

```bash
hermes role info my-advisor
```

Example output:

```
Package: my-advisor
Version: 0.1.0
Status: active
Source: /home/user/my-advisor
Content Hash: sha256:abc123...
Installed: 2026-06-10 14:32:00

Role Contract:
  ID: my_advisor
  Display Name: My Advisor Role
  Family: advisor
  Purpose: Provides strategic advice and guidance

Boundary Mode: advisory

Environment Variables (Consented):
  - API_TOKEN (granted by user)
  - DEBUG_LOG (granted by user)

Included Skills:
  - my-advisor/strategic-thinking
  - my-advisor/risk-analysis

Manifest Validation Warnings:
  (none)
```

### View the Lockfile Directly

```bash
cat ~/.hermes/role-packages/lock.yaml
```

Example:

```yaml
version: 1
packages:
  my-advisor:
    version: 0.1.0
    source: /home/user/my-advisor
    hash: sha256:abc123...
    installed_at: 2026-06-10T14:32:00Z
    status: active
    consents:
      env_vars:
        - API_TOKEN
        - DEBUG_LOG
    validation_warnings: []
```

**Never edit the lockfile manually.** Use `hermes role` commands to manage packages.

## Updating and Removing Packages

### Check for Package Updates

```bash
# If the package is in a git repo:
cd ~/path/to/my-role-package
git fetch
git log --oneline HEAD..origin/main

# If you see new commits, update is available
```

### Update a Package

```bash
# Update the source (e.g., git pull)
cd ~/path/to/my-role-package
git pull origin main

# Reinstall
hermes role install .
```

This will:

1. Validate the new version
2. Prompt for any new environment variables
3. Keep granted consents for existing env vars
4. Update the package payload
5. Update the lockfile

### Remove a Package

```bash
hermes role remove my-advisor
```

This will:

1. Delete the package payload directory
2. Remove the lockfile entry
3. Revoke all environment variable consents
4. Clear caches

Built-in roles are **never** removed and continue to work normally.

## Environment Variable Management

### Understanding Env Variable Consent

Packages can request access to environment variables:

```yaml
# In role-package.yaml
env_requires:
  - name: API_TOKEN
    description: Token for external API
    required: true
  - name: DEBUG_LOG
    description: Debug logging level
    required: false
```

When you install, you grant consent:

```bash
hermes role install . --accept-env API_TOKEN,DEBUG_LOG
```

The agent will **only** pass these variables to the package's skills. Undeclared and unconsented variables are never passed.

### Revoking Env Access

To revoke environment variable access:

1. Remove the package
2. Reinstall without `--accept-env`

```bash
hermes role remove my-advisor
hermes role install ~/my-advisor
# Skip the env variable prompts, or press Enter to decline all
```

### Verifying Consents

```bash
hermes role info my-advisor | grep -A 5 "Environment Variables"
```

Or inspect the lockfile:

```bash
grep -A 10 "my-advisor:" ~/.hermes/role-packages/lock.yaml
```

## Validation and Troubleshooting

### Full Validation Before Install

Always validate before installing into production:

```bash
hermes role validate ./my-role-package
```

This checks:

- manifest schema
- trigger overlap with built-ins
- secret-shaped content
- environment variable declarations
- skill syntax
- role ID uniqueness

### Fixing a Broken Package

If a package is marked `broken` after installation:

```bash
# Check what went wrong
hermes role validate ~/.hermes/role-packages/broken-pkg

# Common issues:
# 1. Manifest syntax error
# 2. Secret content detected
# 3. Trigger overlap with new built-ins (after rebase)
# 4. Skill file corruption

# Fix the package
cd ~/path/to/package
# ... edit files ...

# Reinstall to pick up fixes
hermes role install .
```

### Handling Trigger Overlap Errors

If installation fails with an overlap error:

```
Error: Package trigger 'deploy' overlaps with built-in role 'deployer'
```

Options:

1. **Change the package trigger** — edit `role-package.yaml` to use a unique word
2. **Remove the conflicting built-in** — not recommended, may break scripts
3. **Choose a different package** — if the overlap is legitimate

### Checking for Upstream Rebases

After a Hermes upstream rebase, built-in roles may change. Installed packages remain valid, but check for new conflicts:

```bash
# After rebase, validate all installed packages
for pkg in ~/.hermes/role-packages/*/role-package.yaml; do
  dir=$(dirname "$pkg")
  echo "Validating $dir..."
  hermes role validate "$dir"
done

# If any packages are now broken, update them
```

## Production Deployment

### Pre-Deployment Checklist

Before deploying role packages to production:

- [ ] Validate the package in staging: `hermes role validate ./package`
- [ ] Test the package in a non-critical Hermes instance
- [ ] Review the manifest for any secrets (should find none)
- [ ] Review included skills for hardcoded credentials (should find none)
- [ ] Confirm trigger overlap report is acceptable
- [ ] Document the purpose and any special setup in runbooks

### Safe Rollback

If a package causes issues, rollback is simple:

```bash
# Immediately remove the problem package
hermes role remove problem-package

# Verify routing falls back to built-ins
hermes role list

# Troubleshoot and reinstall when ready
```

Built-in roles continue working throughout, and no service restart is needed.

### Monitoring Package Health

Check periodically:

```bash
# List all packages and their status
hermes role list

# Look for any `broken` packages
# If found, run validation to diagnose:
hermes role validate ~/.hermes/role-packages/<broken-pkg>
```

### Post-Rebase Validation

If Hermes upstream is rebased onto the local branch:

```bash
# After rebase completes, validate all packages
hermes role list  # Should see all packages

# Validate each one
hermes role validate ~/.hermes/role-packages/my-advisor
hermes role validate ~/.hermes/role-packages/my-other-pkg

# If any are broken, reinstall:
hermes role install ~/my-advisor
```

## Tool Category Taxonomy

Valid `allowed_categories` values in manifests:

| Category | Type | Notes |
|---|---|---|
| `read_only_inspection` | Active | Files, search, logs |
| `repo_edit` | Active | File and code mutations |
| `shell_general` | Active | Shell; mutation-capable; approval-gated |
| `production_deploy` | Placeholder | Observe-only; no enforcement |
| `secrets_read` | Placeholder | Observe-only; no enforcement |
| `web_search` | Taxonomy | No enforcement yet (pre-v1) |
| `web_browse` | Taxonomy | No enforcement yet (pre-v1) |
| `job_intel_read` | Taxonomy | No enforcement yet (pre-v1) |

Taxonomy categories are accepted by the manifest validator and produce observe_warn log entries. They will become enforceable when `enforced_tools` mode is activated in v1.

## Safety Guidelines

### Never Put Secrets in Packages

Do not include:

- ❌ Hard-coded API tokens
- ❌ Passwords or database credentials
- ❌ Private key material
- ❌ `.env` files with secrets
- ❌ `auth.json` contents

Instead:

- ✅ Declare env variable names in `env_requires`
- ✅ Let users grant consent at install time
- ✅ Consume values from the environment

The validator **will reject** packages containing secret-shaped content.

### Environment Variables Are Names Only

In `role-package.yaml`, list only the names:

```yaml
env_requires:
  - name: API_TOKEN      # ← Name only, no value
    description: Token for service
    required: false
```

The actual secret is **supplied by the user** at install time via `--accept-env` or environment export.

### Read-Only Package Skills

Skills included in packages are read-only. Hermes cannot:

- Modify or upgrade package skill files
- Override package-owned code
- Edit package manifests

If you want to improve a skill:

1. File an issue or PR with the package maintainer
2. Create a local overlay skill in your Hermes instance
3. Ask the agent to propose improvements in memory (not code edits)

### Overlap Validation

Installation validates that a package's triggers do not conflict with:

- Built-in role keywords
- Other installed packages

Hard errors (install fails):

- Exact trigger match with built-in
- Substring containment in both directions
- Duplicate role ID

If your package has legitimate overlapping triggers, discuss with the package maintainer or create a more specific trigger.

## Observability and Logging

### Check Logs for Package Activity

```bash
# Look for role package validation and loading
grep -i "role.package\|role.registry" ~/.hermes/logs/gateway.log

# Look for observe_warn policy computations
grep -i "observe_warn\|would.block\|policy" ~/.hermes/logs/gateway.log
```

### Understand observe_warn Logs

In MVP, packages with `boundary_mode: observe_warn` will log what they would block (without actually blocking):

```
2026-06-10 15:23:45,123 INFO gateway: [observe_warn] role=my_advisor tool=shell action=exec would_block=true reason="category shell_exec not in allowed"
```

This is **calibration data**. No blocking occurs in MVP. Logs help prepare for production v1 enforcement.

### Sample Observability Queries

```bash
# Count observe_warn events by tool
grep "observe_warn" ~/.hermes/logs/gateway.log | grep -o "tool=[^ ]*" | sort | uniq -c

# Find would-blocks by role
grep "would_block=true" ~/.hermes/logs/gateway.log | grep -o "role=[^ ]*" | sort | uniq -c

# Check package load status
grep "package.*status" ~/.hermes/logs/gateway.log
```

## Advanced: Manual Inspection

### Inspect Package Directory Structure

```bash
tree ~/.hermes/role-packages/my-advisor -L 2

# Output:
# ~/.hermes/role-packages/my-advisor/
# ├── role-package.yaml
# ├── MANIFEST.md
# ├── skills/
# │   ├── strategic-thinking/
# │   │   ├── SKILL.md
# │   │   ├── skill.py
# │   │   └── ...
# │   └── risk-analysis/
# └── ...
```

### Check Lockfile for a Specific Package

```bash
# Show just one package's entry
grep -A 10 "^  my-advisor:" ~/.hermes/role-packages/lock.yaml
```

### Manually Verify No Secrets Were Installed

```bash
# Search package files for secret patterns
grep -r "api.key\|password\|secret\|token=" ~/.hermes/role-packages/ --include="*.yaml" --include="*.py"

# Should return nothing if package is clean
```

## Getting Help

If you encounter issues:

1. **Validate the package** — `hermes role validate <path>`
2. **Check the status** — `hermes role info <name>`
3. **Inspect logs** — `grep "my-role" ~/.hermes/logs/gateway.log`
4. **Review the manifest** — check `role-package.yaml` syntax
5. **Contact the maintainer** — if validation passes but behavior is wrong

For architecture questions, see `/docs/profile-as-package/role-model-concept.md`.

For authoring your own role packages, see [authoring-guide.md](authoring-guide.md).
