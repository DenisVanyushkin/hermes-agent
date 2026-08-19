# Handoff: bounded idea-signal source pipeline

Date: 2026-08-13
Worktree: `/workspace/idea-signal-pipeline`
Branch: `feature/idea-signal-registry`
Commit: `87642b0cc7 feat: add bounded idea signal source pipeline`

## Delivered

- Reviewed source registry: `config/idea_sources.yaml`.
- Bounded RSS/Atom/GitHub JSON collector: `scripts/idea_signal_collector.py`.
- Audited lifecycle CLI: `scripts/idea_source_health.py`.
- Fresh/valid handoff injection in `scripts/idle_idea_context.py`.
- Runtime sync of the registry beside synced scripts in `scripts/sync-runtime-scripts.sh`.
- Lifecycle, parser, status, de-duplication, handoff, and sync tests.
- Operating procedure and initial source list: `docs/idea-sources.md`.

## Verified evidence

- Targeted suite: `38 passed`.
- Ruff: `All checks passed!`.
- `bash -n scripts/sync-runtime-scripts.sh`: passed.
- `python3 -m py_compile scripts/idea_signal_collector.py scripts/idea_source_health.py scripts/idle_idea_context.py`: passed.
- `git diff --check`: passed.
- Runtime-sync smoke in `/tmp/idea-runtime-sync`: collector, source-health, idle context, and `idea_sources.yaml` were copied; 101 runtime scripts synced.
- Collector smoke in an isolated state dir: structured `run_id`, `run_status`, missing baskets, failures, and attempted-source outcomes were emitted.
- Manual source suspension smoke: a candidate source not yet present in health state was seeded from the reviewed registry, suspended with a required reason, and written to health plus JSONL audit files.

## Known environment limitation

Running all `tests/scripts` is blocked during collection by pre-existing missing dependencies in the active base interpreter:

- `httpx` missing in existing skills/index/network tests.
- `defusedxml` missing in the pre-existing `scripts/news_collector.py` tests.

This change set's targeted tests do not require those missing packages and pass.

## Not done by design

- The read-only live checkout `/workspace/live-hermes` was not mutated.
- No live runtime scripts were synced.
- No cron job was updated, enabled, paused, or replaced.
- No production delivery or external write was performed.
- FTC endpoint remains `candidate` because the bounded collector observed HTTP 403; it was not bypassed.

## Next operator gate

Before production activation:

1. Review commit `87642b0cc7` independently.
2. Resolve any review findings.
3. Apply/sync the commit into the intended local-customizations checkout through the normal repository workflow.
4. Sync runtime scripts and registry.
5. Configure the collector cron path and verify downstream `run_id`/`run_status` handoff in a controlled run.
6. Enable the schedule only after controlled verification and explicit approval.
