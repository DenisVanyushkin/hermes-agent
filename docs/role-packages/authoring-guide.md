# Role Package Authoring Guide

This guide shows how to create a Hermes role package from scratch.

## What Is a Role Package?

A role package is a directory containing:

1. **role-package.yaml** — manifest with metadata, role contract, env requirements
2. **skills/** — optional directory with role-specific skills
3. **MANIFEST.md** or docs — optional human-readable package documentation

Once installed, the package contributes a new role to Hermes with:

- unique routing triggers
- defined capabilities and boundaries
- read-only skills
- environment variable access control
- role-context injection for ephemeral behavior shaping

## Example Packages

Two complete examples are in the repo under `tests/fixtures/role_packages/`:

- **hermes-engineer-lab** — `tests/fixtures/role_packages/hermes-engineer-lab-example/` — demonstrates env consent and capping with an optional `SAMPLE_FAKE_TOKEN`; `observe_warn` with `read_only_inspection` and `repo_edit`.
- **hermes-researcher-lab** — `tests/fixtures/role_packages/hermes-researcher-lab-example/` — demonstrates a clean no-env install; `read_only_inspection` only.

Both are regression-tested via `tests/hermes_cli/test_example_role_packages.py` and do not activate routing in MVP.


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

## Quick Start

### Step 1: Create a Package Directory

```bash
mkdir my-first-role
cd my-first-role
```

### Step 2: Write the Manifest

Create `role-package.yaml`:

```yaml
schema_version: 1

package:
  name: my-first-role
  version: 0.1.0
  description: My first role package
  author: Your Name
  license: MIT

role:
  id: my_first_role
  canonical_id: my_first_role
  display_name: My First Role
  role_family: advisor
  
  purpose_summary: |
    A friendly advisor role that helps with general questions.
  
  model_tier_request: standard
  
  persona: |
    You are a friendly, helpful advisor. You provide thoughtful guidance
    on a wide range of topics. You are empathetic and clear.
  
  routing:
    triggers:
      en:
        - help me think through
        - I need advice on
        - advisor
      ru: []
  
  tools:
    allowed_categories:
      - read_only_inspection
    boundary_mode: advisory
  
  boundary_mode: advisory

env_requires: []

```

### Step 3: Validate the Manifest

Before installing, validate:

```bash
hermes role validate .
```

You should see:

```
✓ Manifest schema valid
✓ No secret-shaped content found
✓ No trigger overlap with built-ins
✓ Package structure valid
```

### Step 4: Install and Test

```bash
hermes role install .

# Check it loaded
hermes role list
hermes role info my-first-role
```

Done! Your role package is installed and ready.

## Manifest Reference

A complete `role-package.yaml` has these sections:

### schema_version

Must be `1`.

### package

Top-level package metadata:

```yaml
package:
  name: my-role                    # Unique package name
  version: 1.0.0                   # Semantic version
  description: Brief description   # One line
  author: Your Name                # Package author
  license: MIT                      # SPDX license
  hermes_requires: ">=2024.06"     # Hermes version constraint (optional)
```

### role

The role contract:

```yaml
role:
  id: my_role                       # Unique role ID (alphanumeric + underscore)
  canonical_id: my_role            # Canonical ID (usually same as id)
  display_name: My Role            # Human-readable name
  role_family: advisor             # Role category (advisor, operator, etc.)
  
  purpose_summary: |               # One sentence
    What this role does and why.
  
  model_tier_request: standard     # "standard" or "advanced"
  
  persona: |                       # Optional: persona text for context injection
    You are a [role]. [Personality]. [Boundaries].
  
  routing:
    triggers:
      en:                          # English triggers
        - keyword one
        - keyword two
        - multi word phrase
      ru: []                       # Russian triggers (empty = not localized)
  
  tools:
    allowed_categories:            # List of tool category strings
      - read_only_inspection
      - web_search
    boundary_mode: advisory        # "advisory", "observe_warn", or "enforced_tools"
```

### env_requires

Environment variables the package needs:

```yaml
env_requires:
  - name: API_TOKEN                # Variable name (uppercase)
    description: Token for service  # Human-readable description
    required: true                 # Whether installation fails without it
  
  - name: DEBUG_LOG
    description: Enable debug logging
    required: false
```

Users grant consent at install time:

```bash
hermes role install . --accept-env API_TOKEN,DEBUG_LOG
```

## Package Skills

Skills are role-specific implementations. They go in a `skills/` directory:

```
my-first-role/
├── role-package.yaml
├── MANIFEST.md
└── skills/
    └── advisor-insights/
        ├── SKILL.md
        ├── skill.py
        └── requirements.txt (optional)
```

### Skill Structure

**skills/advisor-insights/SKILL.md:**

```markdown
# Advisor Insights

Provides strategic insights on complex problems.

## Usage

`/advisor-insights <problem description>`

## Examples

- `/advisor-insights career change decision`
- `/advisor-insights project planning`

## Environment Variables

Uses (optional): `DEBUG_LOG`

## Related Skills

- advisor-empathy
- problem-analysis
```

**skills/advisor-insights/skill.py:**

```python
import os
import json

def advisor_insights(problem: str) -> dict:
    """
    Generate insights on a problem.
    """
    debug = os.environ.get("DEBUG_LOG", "").lower() == "true"
    
    if debug:
        print(f"[DEBUG] Analyzing problem: {problem}")
    
    # Your logic here
    insights = {
        "analysis": "...",
        "recommendations": ["...", "..."]
    }
    
    return insights
```

**skills/advisor-insights/requirements.txt:**

```
requests>=2.28.0
```

### Skill Notes

- Skills are **read-only** at runtime; Hermes cannot modify them
- Env variables must be declared in both:
  - The skill's `SKILL.md`
  - The manifest's `env_requires`
- Users must consent to env variables at install time
- Skills are mounted from the package directory; they're not copied

## Routing Triggers

Triggers are keywords that route conversations to your role.

### Good Trigger Examples

```yaml
routing:
  triggers:
    en:
      - advisor
      - I need advice
      - help me think through
      - strategic perspective
      - advisor role
```

### Bad Trigger Examples

```yaml
routing:
  triggers:
    en:
      - a               # Too broad; will match almost everything
      - help            # Conflicts with built-in "help" keyword
      - deploy prod     # Too specific; no one will use it
```

### Overlap Rules

Your triggers must **not**:

- Exactly match built-in trigger keywords
- Be substrings of other installed packages' triggers
- Contain other packages' triggers as substrings

Validator will reject overlapping triggers at install time. If you get an overlap error:

1. Make your triggers more specific
2. Remove a conflicting package
3. Rename your role to avoid family conflicts

Example conflict detection:

```
❌ "deploy" overlaps with built-in "deployer" (substring)
✓ "deploy-advisory" is unique
```

## Boundary Modes

Your package declares one boundary mode:

### advisory

The role shapes behavior but does not constrain a misbehaving model at dispatch.

Use for:

- guidance roles
- roles that provide context/persona
- roles that are primarily informational

```yaml
boundary_mode: advisory
```

Honest guarantee:

> This role shapes behavior but does not constrain a misbehaving or prompt-injected model at dispatch time.

### observe_warn

The system logs what it would block, but does not block.

Use for:

- security-sensitive roles (while calibrating)
- roles that will eventually enforce policy
- roles in transition from advisory

```yaml
boundary_mode: observe_warn
```

Behavior:

- Agent computes effective policy
- Agent logs would-be denials
- Agent does **not** block calls (MVP)
- Useful for calibration and false-positive detection

### enforced_tools

The system enforces tool allow-sets at dispatch.

**Note: Not implemented in MVP.** Available in production v1 after observe_warn calibration.

```yaml
boundary_mode: enforced_tools

tools:
  allowed_categories:
    - read_only_inspection
    - web_search
```

## Tool Categories

When declaring `allowed_categories`, use these standard category names:

### Read-Only Inspection

```yaml
allowed_categories:
  - read_only_inspection
```

File reads, directory listings, logs, audit trails, non-destructive queries.

### Web Search and Browse

```yaml
allowed_categories:
  - web_search
  - web_browse
```

HTTP requests, search engines, scraping.

### Communication

```yaml
allowed_categories:
  - slack_send
  - email_send
  - message_send
```

Sending messages to channels, email, Slack, etc.

### Admin and Monitoring

```yaml
allowed_categories:
  - docker_diagnostics
  - system_inspect
```

Non-destructive system inspection, logs, metrics.

### Informational

```yaml
allowed_categories:
  - inventing_facts
  - research
```

Roles allowed to be exploratory or speculative.

### Web Search

```yaml
allowed_categories:
  - web_search
```

Read-only web search and query tools (`web_search`, `web_extract`).

### Web Browse

```yaml
allowed_categories:
  - web_browse
```

Read-only browser navigation and page inspection (`browser_navigate`, `browser_snapshot`, etc.).

### Job Intel Read

```yaml
allowed_categories:
  - job_intel_read
```

Read-only access to job-intelligence pipeline data and state. No writes to job pipeline.

**Enforcement note:** In MVP, categories are advisory only. In v1, they become enforced at dispatch for `enforced_tools` boundary mode. The categories `web_search`, `web_browse`, and `job_intel_read` are taxonomy additions from the pre-v1 calibration pass — accepted by validation but not yet enforced.

**`shell_general` note:** Mutation-capable. Declaring it in a manifest does not bypass or lower the existing approval gate at the tool/action boundary. All shell actions still require the same user approval as built-in roles.

## Complete Example

Here's a complete minimal role package:

```
example-advisor/
├── role-package.yaml
├── MANIFEST.md
└── skills/
    └── example-skill/
        ├── SKILL.md
        └── skill.py
```

**role-package.yaml:**

```yaml
schema_version: 1

package:
  name: example-advisor
  version: 0.1.0
  description: Example advisor role package
  author: Example Author
  license: MIT

role:
  id: example_advisor
  canonical_id: example_advisor
  display_name: Example Advisor
  role_family: advisor
  
  purpose_summary: |
    Provides helpful guidance on common questions.
  
  model_tier_request: standard
  
  persona: |
    You are a thoughtful advisor. You provide clear, empathetic guidance.
  
  routing:
    triggers:
      en:
        - get advisor guidance
        - I need thoughtful advice
        - advisor perspective
      ru: []
  
  tools:
    allowed_categories:
      - read_only_inspection
      - web_search
    boundary_mode: advisory

env_requires:
  - name: EXAMPLE_API_KEY
    description: Optional API key for example service
    required: false
```

**MANIFEST.md:**

```markdown
# Example Advisor Role Package

Provides helpful guidance on common questions through thoughtful analysis.

## Installation

```bash
hermes role install ./example-advisor
```

## Configuration

Optional environment variable:

```bash
hermes role install ./example-advisor --accept-env EXAMPLE_API_KEY
```

## Usage

Route to this role with keywords:
- "get advisor guidance"
- "I need thoughtful advice"
- "advisor perspective"

## Skills Included

- example-skill: Core advisory skill

## Boundary Model

Advisory. Shapes behavior but does not enforce tool restrictions.

## Author

Example Author
```

**skills/example-skill/SKILL.md:**

```markdown
# Example Skill

Provides example functionality.

## Usage

`/example-skill <input>`

## Environment Variables

Uses (optional): `EXAMPLE_API_KEY`
```

**skills/example-skill/skill.py:**

```python
import os

def example_skill(query: str) -> str:
    api_key = os.environ.get("EXAMPLE_API_KEY", "")
    return f"Example response to: {query}"
```

## Validation and Testing

### Validate Before Installing

```bash
hermes role validate ./example-advisor
```

Checks:

- ✓ Manifest schema
- ✓ No secrets
- ✓ No overlapping triggers
- ✓ Env var declarations
- ✓ Skill structure
- ✓ File permissions

### Test Installation

```bash
# Install locally
hermes role install ./example-advisor

# List to verify
hermes role list

# Get details
hermes role info example_advisor

# Remove after testing
hermes role remove example_advisor
```

### Common Validation Errors

**Error: "secret-shaped content detected"**

Check for:
- Hard-coded API keys or tokens
- Password strings
- Credential files
- Anything matching `api.key`, `password`, `secret`, etc.

**Fix:** Remove secrets and use `env_requires` instead.

**Error: "trigger 'deploy' overlaps with built-in"**

Your trigger conflicts with a built-in role. Choose a different trigger or add context:

```yaml
# Bad
triggers:
  en: [deploy]

# Good
triggers:
  en: [deploy-advisor, advise on deployment]
```

**Error: "manifest schema invalid"**

Validate YAML syntax:

```bash
# Check YAML syntax
python3 -m yaml < role-package.yaml
```

Look for:
- Indentation issues (must be spaces, not tabs)
- Missing colons or quotes
- Invalid list/dict structure

## Best Practices

### 1. Never Include Secrets

❌ Bad:

```yaml
env_requires:
  - name: API_TOKEN
    value: "sk-abc123..."  # Hard-coded!
```

✓ Good:

```yaml
env_requires:
  - name: API_TOKEN
    description: Token for service
    required: false
```

### 2. Write Clear Descriptions

```yaml
env_requires:
  - name: POSTGRES_URL
    description: PostgreSQL connection string (postgresql://user:pass@host/db)
    required: true
```

### 3. Keep Triggers Unique

Test against built-ins:

```bash
# Manually check these don't match built-in keywords
# (validator will catch it, but better to catch early)
grep -r "help me think through" /path/to/hermes/hermes_cli/
```

### 4. Document Skills Thoroughly

Include:

- What the skill does
- How to use it (`/skill-name <args>`)
- What environment variables it needs
- Related skills

### 5. Use Semantic Versioning

```
0.1.0 — initial release
0.2.0 — new feature
1.0.0 — stable, compatible release
1.1.0 — backwards-compatible feature
2.0.0 — breaking change
```

### 6. License Your Package

Use an SPDX license identifier:

```yaml
license: MIT
```

Common choices:

- `MIT` — permissive, widely used
- `Apache-2.0` — permissive, explicit patent grant
- `GPL-3.0` — copyleft
- `Proprietary` — not open source

## Directory Checklist

Before installation, verify:

- [ ] `role-package.yaml` exists and is valid YAML
- [ ] `schema_version: 1`
- [ ] `package.name` is unique (check with `hermes role list`)
- [ ] `role.id` is unique
- [ ] `role.routing.triggers` are not empty (en or ru)
- [ ] `boundary_mode` is one of: `advisory`, `observe_warn`, `enforced_tools`
- [ ] `env_requires` list is complete and accurate
- [ ] Skills in `skills/` have `SKILL.md` and `.py` files
- [ ] No hard-coded secrets anywhere
- [ ] No `.env` or `auth.json` files
- [ ] Optional `MANIFEST.md` is clear and complete

## Publishing and Sharing

### Version Control

Publish your package in a git repository:

```bash
git init
git add .
git commit -m "Initial release of my-role-package"
git remote add origin https://github.com/you/my-role-package
git push -u origin main
```

### Installation from Git

Users can install directly from your repo:

```bash
git clone https://github.com/you/my-role-package.git
hermes role install ./my-role-package
```

Or from a release archive:

```bash
wget https://github.com/you/my-role-package/releases/download/v0.1.0/my-role-package.tar.gz
tar -xzf my-role-package.tar.gz
hermes role install ./my-role-package
```

### Documentation

Include:

- `README.md` — overview and usage
- `MANIFEST.md` — role details and boundaries
- `docs/` — detailed guide if complex
- Examples in `skills/SKILL.md`

## Next Steps

- **Install and test locally** — `hermes role install ./my-role`
- **Share with team** — push to a git repo
- **Gather feedback** — refine triggers and skills
- **Plan for v1** — if you need tool enforcement, plan for that in v1

For operational questions, see [operator-runbook.md](operator-runbook.md).

For architecture details, see `/docs/profile-as-package/role-model-concept.md`.
