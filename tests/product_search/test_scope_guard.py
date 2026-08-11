from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_product_search_scope.sh"
PROTECTED = (
    "job_intel/sources.py",
    "job_intel/ats_sources.py",
    "job_intel/browser_sourcing.py",
    "job_intel/browser_worker.py",
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "local/customizations")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "user.name", "Product Search Tests")
    for index, relative in enumerate(PROTECTED):
        write(repo / relative, f"protected-{index}\n")
    write(repo / "config/product_search/source_capabilities.v1.yaml", "version: 1\n")
    write(repo / "scripts/check_product_search_scope.sh", SCRIPT.read_text(encoding="utf-8"))
    os.chmod(repo / "scripts/check_product_search_scope.sh", 0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: seed base")
    base_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "codex/job-intel-product-search")

    manifest = tmp_path / "scope-baseline.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "base_branch": "local/customizations",
                "base_commit": base_commit,
                "protected_paths": {
                    relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
                    for relative in PROTECTED
                },
                "production_source_config_paths": [
                    "config/product_search/source_capabilities.v1.yaml"
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return repo, manifest


def run_guard(repo: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PRODUCT_SEARCH_REPO_ROOT"] = str(repo)
    env["PRODUCT_SEARCH_SCOPE_BASELINE"] = str(manifest)
    return subprocess.run(
        ["bash", str(repo / "scripts/check_product_search_scope.sh")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )


def test_guard_accepts_clean_feature_diff(tmp_path: Path) -> None:
    repo, manifest = make_repo(tmp_path)
    write(repo / "job_intel/product_search/new_module.py", "VALUE = 1\n")

    result = run_guard(repo, manifest)

    assert result.returncode == 0, result.stderr
    assert "scope guard passed" in result.stdout


def test_guard_rejects_feature_change_to_protected_scraper(tmp_path: Path) -> None:
    repo, manifest = make_repo(tmp_path)
    write(repo / "job_intel/sources.py", "feature mutation\n")

    result = run_guard(repo, manifest)

    assert result.returncode != 0
    assert "protected Product Search path changed: job_intel/sources.py" in result.stderr


def test_guard_rejects_wrong_branch(tmp_path: Path) -> None:
    repo, manifest = make_repo(tmp_path)
    git(repo, "switch", "local/customizations")

    result = run_guard(repo, manifest)

    assert result.returncode != 0
    assert "expected branch codex/job-intel-product-search" in result.stderr


def test_reviewed_upstream_change_can_be_repinned_after_rebase(tmp_path: Path) -> None:
    repo, old_manifest = make_repo(tmp_path)
    git(repo, "switch", "local/customizations")
    write(repo / "job_intel/sources.py", "reviewed upstream mutation\n")
    git(repo, "add", "job_intel/sources.py")
    git(repo, "commit", "-m", "upstream: mutate protected source")
    new_base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "codex/job-intel-product-search")
    git(repo, "rebase", "local/customizations")

    stale = run_guard(repo, old_manifest)
    assert stale.returncode != 0
    assert "scope baseline base_commit does not match merge-base" in stale.stderr

    repinned = old_manifest.with_name("repinned.yaml")
    payload = yaml.safe_load(old_manifest.read_text(encoding="utf-8"))
    payload["base_commit"] = new_base
    payload["protected_paths"]["job_intel/sources.py"] = hashlib.sha256(
        (repo / "job_intel/sources.py").read_bytes()
    ).hexdigest()
    repinned.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    accepted = run_guard(repo, repinned)
    assert accepted.returncode == 0, accepted.stderr
    assert "scope guard passed" in accepted.stdout
