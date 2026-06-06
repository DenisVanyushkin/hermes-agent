from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from job_intel.observability import JobIntelObservabilityExporter
from job_intel.runtime import resolve_db_path
from job_intel.store import JobIntelStore


def _log_path_state(logger: logging.Logger, path: Path) -> None:
    try:
        exists = path.exists()
    except Exception as exc:
        logger.info("path=%s exists=error error=%s", path, exc)
        return
    logger.info("path=%s exists=%s", path, exists)
    if not exists:
        return
    try:
        st = path.stat()
        logger.info("path=%s size=%s mode=%s uid=%s gid=%s", path, st.st_size, oct(st.st_mode & 0o777), st.st_uid, st.st_gid)
    except Exception as exc:
        logger.info("path=%s stat=error error=%s", path, exc)
    try:
        if path.is_dir():
            entries = sorted(p.name for p in path.iterdir())[:20]
            logger.info("path=%s dir_entries=%s", path, entries)
    except Exception as exc:
        logger.info("path=%s listdir=error error=%s", path, exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("job_intel.exporter")
    parser = argparse.ArgumentParser(description="Job Intel Prometheus metrics exporter")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9899)
    args = parser.parse_args()

    db_path = resolve_db_path()
    logger.info(
        "startup cwd=%s uid=%s gid=%s db_path=%s env_JOB_INTEL_DB_PATH=%s env_JOB_INTEL_WORKDIR=%s",
        os.getcwd(),
        os.getuid(),
        os.getgid(),
        db_path,
        os.getenv("JOB_INTEL_DB_PATH"),
        os.getenv("JOB_INTEL_WORKDIR"),
    )
    for candidate in [
        db_path,
        db_path.parent,
        Path("/root/.hermes/job_intel/job_intel.sqlite3"),
        Path("/root/.hermes/job_intel"),
        Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3"),
        Path("/home/hermes/.hermes/job_intel"),
        Path("/workspace/live-hermes/.hermes/job_intel/job_intel.sqlite3"),
        Path("/workspace/live-hermes/.hermes/job_intel"),
    ]:
        _log_path_state(logger, candidate)

    exporter = JobIntelObservabilityExporter(JobIntelStore(db_path))
    exporter.serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
