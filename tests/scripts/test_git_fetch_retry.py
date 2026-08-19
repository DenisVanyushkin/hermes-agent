"""The upstream fetch must survive GitHub's transient 429s.

GitHub applies a secondary, IP-scoped rate limit to git-upload-pack on this
host: roughly half the ref advertisements come back HTTP 429 regardless of
whether GITHUB_TOKEN is presented (api.github.com/rate_limit stays untouched).
An unguarded fetch turns that coin flip into a lost sync cycle -- the preflight
exits 128 and the next scheduled attempt is 4320 minutes away, which is exactly
how the 2026-08-19 run died three times in a row.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "git-retry.sh"


def _fake_git(tmp_path: Path, script: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    git = bindir / "git"
    git.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(script), encoding="utf-8")
    git.chmod(0o755)
    return bindir


def _run(tmp_path: Path, bindir: Path, body: str) -> subprocess.CompletedProcess:
    runner = tmp_path / "runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\nset -uo pipefail\nsource %s\n%s\n" % (LIB, textwrap.dedent(body)),
        encoding="utf-8",
    )
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HERMES_GIT_FETCH_RETRY_DELAY": "0",
        "COUNTER": str(tmp_path / "count"),
    }
    return subprocess.run(
        ["bash", str(runner)], capture_output=True, text=True, env=env, timeout=60
    )


class TestGitFetchRetry:
    def test_retries_through_a_429_and_then_succeeds(self, tmp_path):
        bindir = _fake_git(
            tmp_path,
            """
            n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNTER"
            if [ "$n" -lt 3 ]; then
              echo "error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429" >&2
              exit 128
            fi
            echo "fetched"
            """,
        )
        proc = _run(tmp_path, bindir, 'git_fetch_retry /repo https://example.invalid/x.git "+refs/heads/main:refs/remotes/origin/main"')
        assert proc.returncode == 0, proc.stderr
        assert (tmp_path / "count").read_text().strip() == "3"

    def test_gives_up_after_the_configured_attempts(self, tmp_path):
        bindir = _fake_git(
            tmp_path,
            """
            n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNTER"
            echo "error: RPC failed; HTTP 429" >&2
            exit 128
            """,
        )
        proc = _run(
            tmp_path,
            bindir,
            'HERMES_GIT_FETCH_RETRIES=4 git_fetch_retry /repo https://example.invalid/x.git "+refs/heads/main:refs/remotes/origin/main"',
        )
        assert proc.returncode != 0
        assert (tmp_path / "count").read_text().strip() == "4"

    def test_does_not_retry_a_permanent_failure(self, tmp_path):
        """A dead repo or a bad credential must fail on the first try.

        Retrying these only delays the report by minutes and hides the real
        cause behind a wall of identical attempts.
        """
        bindir = _fake_git(
            tmp_path,
            """
            n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNTER"
            echo "fatal: repository 'https://example.invalid/x.git/' not found" >&2
            exit 128
            """,
        )
        proc = _run(tmp_path, bindir, 'git_fetch_retry /repo https://example.invalid/x.git "+refs/heads/main:refs/remotes/origin/main"')
        assert proc.returncode != 0
        assert (tmp_path / "count").read_text().strip() == "1"

    def test_reports_each_transient_attempt_on_stderr(self, tmp_path):
        """Silent retries would make a slow sync look like a hung one."""
        bindir = _fake_git(
            tmp_path,
            """
            n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNTER"
            if [ "$n" -lt 2 ]; then echo "error: RPC failed; HTTP 429" >&2; exit 128; fi
            """,
        )
        proc = _run(tmp_path, bindir, 'git_fetch_retry /repo https://example.invalid/x.git "+refs/heads/main:refs/remotes/origin/main"')
        assert proc.returncode == 0
        assert "429" in proc.stderr or "retry" in proc.stderr.lower()


class TestCallSitesUseTheRetry:
    """Both network fetches of the sync path must go through the helper."""

    @pytest.mark.parametrize(
        "rel",
        [
            "scripts/preflight-local-customizations-update.sh",
            "scripts/sync-local-customizations.sh",
        ],
    )
    def test_upstream_fetch_is_guarded(self, rel):
        text = (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
        assert "git-retry.sh" in text
        unguarded = [
            line.strip()
            for line in text.splitlines()
            if "fetch --prune" in line and "git_fetch_retry" not in line and not line.strip().startswith("#")
        ]
        assert unguarded == []
