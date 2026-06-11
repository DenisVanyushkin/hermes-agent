# Hermes Role Packages

Role packages are distributable units that contribute a role/persona to a running Hermes agent instance. They enable safe, modular role extension without forking the agent or requiring upstream acceptance.

## What Are Role Packages?

A role package bundles:

- **role contract** — persona, boundaries, output style for a specific role
- **routing triggers** — keywords that route conversations to this role
- **manifest** — metadata, env requirements, skill declarations, boundary mode
- **package skills** — read-only skill implementations specific to this role
- **docs** — role-specific guidance and limitations

Once installed, a role package:

- registers its role in the role registry;
- enables its routing triggers;
- provides its skills on-demand;
- respects user consent for environment variable passthrough;
- operates under one of three boundary modes: `advisory`, `observe_warn`, or `enforced_tools` (MVP: `advisory` and `observe_warn` only).

## MVP Capabilities (Current)

### ✅ What Works Now

- **Install / Remove** — `hermes role install <path>` and `hermes role remove <name>`
- **Validation** — comprehensive manifest validation, trigger overlap detection, env requirement checks
- **Listing and Inspection** — `hermes role list` and `hermes role info <name>` to view installed roles
- **Package Skills** — skills included in packages are mounted as read-only and indexed properly
- **Env Consent** — users grant env variable access by name at install time; consent is recorded in lockfile
- **Observe Warn** — system logs would-be tool denials without blocking (calibration mode)
- **Fail-Safe Registry** — one bad package cannot break built-in roles
- **Lockfile State** — package metadata and user consents persist across restarts

### ⚠️ Intentionally Not Implemented Yet (MVP)

- **Package Routing** — packages are installed and validated, but their routing triggers are not active; built-in routing is unchanged
- **Enforced Tool Blocking** — `enforced_tools` mode is validated in manifests but does not block calls
- **Argument-Level Enforcement** — command-string patterns not enforced
- **Path Scope Enforcement** — file read/write scopes not enforced
- **Package Signing** — no trust verification or signatures
- **Marketplace** — no distribution or sharing mechanism
- **Role-Scoped Memory** — memory is global, not per-role

These features are planned for production v1 and beyond.

## Storage Layout

Installed role packages are stored outside the repository:

```
~/.hermes/role-packages/
├── <package-name>/           # Package payload
│   ├── role-package.yaml     # Manifest
│   ├── MANIFEST.md           # Optional docs
│   └── skills/
│       └── <skill-name>/
│           ├── SKILL.md
│           └── skill.py
└── lock.yaml                 # Lockfile (package metadata + consents)
```

The lockfile never stores secret values. It records:

- package name, version, source, hash
- installed time and status
- user consents for environment variables
- validation warnings

## Core Commands

### Install a Package

```bash
hermes role install /path/to/my-role-package

# Or with env consent pre-agreed:
hermes role install /path/to/my-role-package --accept-env MY_TOKEN,MY_SECRET
```

The installer will:

1. validate the manifest
2. check for trigger overlap with built-ins
3. scan for secret-shaped content
4. prompt for env variable consent
5. write the package and lockfile
6. clear caches so skills load

### List Installed Packages

```bash
hermes role list
```

Shows:

- package name and version
- status (`active`, `broken`, `disabled`)
- role ID and display name
- any validation warnings

### Get Package Details

```bash
hermes role info <package-name>
```

Shows:

- full manifest (excluding secrets)
- installed time and source
- granted consents
- included skills
- role contract excerpt

### Validate a Package (Without Installing)

```bash
# Full validation
hermes role validate /path/to/package

# Schema only (lightweight check)
hermes role validate /path/to/package --schema-only
```

### Remove a Package

```bash
hermes role remove <package-name>
```

Cleans up:

- package files
- lockfile entry
- user consents
- skill caches

## Safety Model

### Built-In Roles Always Work

A bad or incompatible role package cannot break:

- routing to built-in roles
- existing tool behavior
- any model policy

If a package is invalid, it is marked `broken` and silently skipped.

### No Secrets in Packages

Packages must not include:

- hard-coded API tokens or credentials
- `.env` contents
- `auth.json`
- encrypted secret material
- anything that looks like a secret pattern

The validator rejects packages with secret-shaped content.

### Environment Variable Consent

Packages can request environment variable access by name:

```yaml
env_requires:
  - name: MY_API_TOKEN
    description: Optional token for external service
    required: false
```

Users grant consent **at install time**:

```bash
hermes role install <package> --accept-env MY_API_TOKEN
```

Consent is recorded in the lockfile. The agent only passes the env var to the package's skills if:

1. the package manifest declares it;
2. the skill declares it uses the env var;
3. the user consented to it.

Undeclared, unconsented, and wildcard env access is rejected.

### Read-Only Package Skills

Package skills are mounted as read-only. The agent's Curator and self-improvement subsystems cannot:

- modify package skill files
- upgrade package content
- override package-owned implementations

User-owned improvements to roles should become:

- memory notes
- local skill overlays
- issue/PR suggestions
- separate user-owned skills

### No Second Approval System

Role packages do not create a separate approval system. Intent-level and command-level approvals use the existing host approval machinery.

## Current Limitations

### Boundary Mode: Advisory and Observe-Warn Only

MVP supports two boundary modes:

- **advisory** — the role shapes behavior but does not constrain a misbehaving model at dispatch
- **observe_warn** — the system logs what it would block, but does not actually block (calibration phase)

**Enforced_tools** mode (which blocks denied tool calls) is not active in MVP. It will be implemented in production v1 after observe_warn calibration.

### Routing Triggers Inactive

Package routing triggers are validated and stored, but are not active in MVP. Built-in routing is unchanged.

In v1, valid package triggers will merge into the data-driven routing layer.

### No Tool-Level Enforcement

`enforced_tools` in manifests is validated but not enforced. The agent currently cannot prevent a model from calling a tool.

Enforcement will be added in production v1 via a ContextVar-based per-turn policy.

### No Package Marketplace or Signing

Packages are local directories or git repos; there is no central marketplace or signature verification.

Trust is currently based on source and code review. In future work, we may add optional signing and verification.

## Common Workflows

### Author and Test a Role Package Locally

See [authoring-guide.md](authoring-guide.md) for step-by-step instructions on creating a role package.

### Install a Community Package

If you have a directory or git repo with a `role-package.yaml`:

```bash
git clone https://github.com/example/my-role my-role
hermes role install ./my-role
```

### Enable Environment Variable Access

To let a package access a secret token:

```bash
export MY_API_TOKEN="your-secret-here"
hermes role install ./my-role --accept-env MY_API_TOKEN
```

The token is **not** stored in the lockfile; only the fact that you granted consent is recorded.

### Audit Current Consents

```bash
cat ~/.hermes/role-packages/lock.yaml
```

Shows which environment variables each package has permission to access.

### Revoke Consents

Remove and reinstall the package to revoke all consents, then install without `--accept-env`:

```bash
hermes role remove my-role
hermes role install ./my-role  # No env access
```

## Troubleshooting

### Package Marked as "broken"

If `hermes role list` shows a package with status `broken`, check:

1. was the package modified on disk after install?
2. does the manifest have an invalid schema?
3. do any skills have syntax errors?

Run validation to see details:

```bash
hermes role validate ~/.hermes/role-packages/<package-name>
```

### Overlapping Triggers

If installation fails with "overlap error," the package's routing triggers conflict with:

- built-in role keywords
- existing package triggers

Modify the package manifest to use unique triggers, or remove the conflicting installed package.

### Env Variable Not Passed Through

Check the skill environment requirements and the lockfile:

1. is the env var declared in the package manifest?
2. does the skill declare that it uses the env var?
3. is the env var in the lockfile under `consents`?

If all three are true, the env var should be passed. If not, reinstall with `--accept-env`.

### Skills Not Loading

Reinstall the package to clear the skill index:

```bash
hermes role remove my-role
hermes role install ./my-role
```

Then verify skills appear:

```bash
hermes role info my-role
```

## Production v1 Roadmap

The next major version will add:

- **Enforced tool blocking** — `enforced_tools` mode will prevent denied calls at dispatch
- **Routing activation** — package routing triggers will be active alongside built-ins
- **Category-to-tool mapping** — enforcement table mapping role categories to actual tools/toolsets
- **ContextVar per-turn policy** — enforcement propagates through concurrent execution
- **MCP/plugin default deny** — unknown plugins/MCP tools are denied by default under enforced roles
- **Live calibration gate** — observe_warn must run for at least 1 week with near-zero false positives before enforced_tools is enabled


## Example Role Packages

Two reference implementations are included under :

| Package | Path |
|---|---|
|  |  |
|  |  |

### hermes-engineer-lab

Demonstrates **env consent and capping**:

- declares an optional  in 
- shows correct  install flow
- uses  boundary mode with  and  categories
- includes the  skill

### hermes-researcher-lab

Demonstrates **clean no-env install**:

- no ; installs without consent prompts
-  only
-  boundary mode
- includes the  skill

Both packages use unique role IDs (, ) that do not shadow any built-in roles. Their routing triggers are validated but **not active** in MVP — built-in routing is unchanged.

These examples are regression-tested via .

## Architecture and Security Details

See [operator-runbook.md](operator-runbook.md) for operational command reference.

See [authoring-guide.md](authoring-guide.md) for detailed manifest and skill examples.

For deep technical details, see `/docs/profile-as-package/role-model-concept.md`.
