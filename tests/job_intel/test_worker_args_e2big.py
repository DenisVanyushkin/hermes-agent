from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from job_intel import browser_worker, sources


def _patch_linkedin_prerequisites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "browser_native_available", lambda: True)
    monkeypatch.setattr(sources, "_ensure_required_browser_profile", lambda *_args: None)
    monkeypatch.setattr(sources, "_browser_config", lambda _source: object())


def test_linkedin_payload_files_keep_3000_urls_out_of_argv_and_are_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_linkedin_prerequisites(monkeypatch)
    captured: dict[str, object] = {}

    def fake_worker(*args: str, **_kwargs: object) -> dict[str, object]:
        captured["args"] = args
        paths = {args[index + 1] for index, arg in enumerate(args) if arg in {
            "--detail-skip-urls-file", "--detail-first-seen-at-file"
        }}
        captured["paths"] = paths
        captured["modes"] = {path: stat.S_IMODE(os.stat(path).st_mode) for path in paths}
        captured["parents"] = {path: stat.S_IMODE(Path(path).parent.stat().st_mode) for path in paths}
        captured["contents"] = {path: Path(path).read_text(encoding="utf-8") for path in paths}
        return {"vacancies": [], "session_health": {}, "search_trace": {}}

    monkeypatch.setattr(sources, "_browser_worker_payload", fake_worker)
    urls = {f"https://www.linkedin.com/jobs/view/{index}" for index in range(3000)}
    first_seen = {url: "2026-09-18T07:00:00+00:00" for url in urls}

    sources.fetch_linkedin_vacancies(
        "VP Product",
        detail_skip_urls=urls,
        detail_first_seen_at=first_seen,
    )

    args = captured["args"]
    assert isinstance(args, tuple)
    assert "--detail-skip-urls-json" not in args
    assert "--detail-first-seen-at-json" not in args
    assert max(len(arg.encode("utf-8")) for arg in args) < 131072
    assert captured["modes"] == {path: 0o600 for path in captured["paths"]}
    assert captured["parents"] == {path: 0o700 for path in captured["paths"]}
    assert all(not Path(path).exists() for path in captured["paths"])

    contents = captured["contents"]
    assert isinstance(contents, dict)
    decoded = {path: json.loads(value) for path, value in contents.items()}
    skip_path = next(path for path in decoded if isinstance(decoded[path], list))
    first_seen_path = next(path for path in decoded if isinstance(decoded[path], dict))
    assert set(decoded[skip_path]) == urls
    assert decoded[first_seen_path] == first_seen


@pytest.mark.parametrize(
    "failure",
    [
        sources.SourceFetchError("worker failed"),
        subprocess.TimeoutExpired(["worker"], timeout=1),
    ],
    ids=["exception", "timeout"],
)
def test_linkedin_payload_files_are_removed_when_worker_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    _patch_linkedin_prerequisites(monkeypatch)
    paths: set[str] = set()

    def failing_worker(*args: str, **_kwargs: object) -> dict[str, object]:
        paths.update(
            args[index + 1]
            for index, arg in enumerate(args)
            if arg in {"--detail-skip-urls-file", "--detail-first-seen-at-file"}
        )
        raise failure

    monkeypatch.setattr(sources, "_browser_worker_payload", failing_worker)
    with pytest.raises(type(failure)):
        sources.fetch_linkedin_vacancies(
            "VP Product",
            detail_skip_urls={"https://www.linkedin.com/jobs/view/1"},
            detail_first_seen_at={"https://www.linkedin.com/jobs/view/1": "now"},
        )

    assert paths
    assert all(not Path(path).exists() for path in paths)
    assert all(not Path(path).parent.exists() for path in paths)


def test_browser_worker_reads_file_payloads_and_keeps_legacy_json_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: list[dict[str, object]] = []

    def fake_run_linkedin(_query: str, **kwargs: object):
        captured.append(kwargs)
        return [], {}, {}

    monkeypatch.setattr(browser_worker, "_run_linkedin", fake_run_linkedin)
    skip_urls = ["https://www.linkedin.com/jobs/view/1"]
    first_seen = {skip_urls[0]: "now"}
    skip_file = tmp_path / "skip.json"
    first_seen_file = tmp_path / "first-seen.json"
    skip_file.write_text(json.dumps(skip_urls), encoding="utf-8")
    first_seen_file.write_text(json.dumps(first_seen), encoding="utf-8")

    assert browser_worker.main(
        [
            "linkedin",
            "VP Product",
            "1",
            "--detail-skip-urls-file",
            str(skip_file),
            "--detail-first-seen-at-file",
            str(first_seen_file),
        ]
    ) == 0
    assert captured[-1]["detail_skip_urls"] == set(skip_urls)
    assert captured[-1]["detail_first_seen_at"] == first_seen

    assert browser_worker.main(
        [
            "linkedin",
            "VP Product",
            "1",
            "--detail-skip-urls-json",
            json.dumps(skip_urls),
            "--detail-first-seen-at-json",
            json.dumps(first_seen),
        ]
    ) == 0
    assert captured[-1]["detail_skip_urls"] == set(skip_urls)
    assert captured[-1]["detail_first_seen_at"] == first_seen
    capsys.readouterr()


def test_browser_worker_rejects_oversized_argv_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_python = tmp_path / "python"
    browser_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("JOB_INTEL_BROWSER_PYTHON", str(browser_python))
    monkeypatch.setattr(
        sources.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not run"),
    )

    with pytest.raises(sources.SourceFetchError, match="--execution-plan-json"):
        sources._browser_worker_payload(
            "linkedin",
            "--execution-plan-json",
            "x" * 100001,
        )
