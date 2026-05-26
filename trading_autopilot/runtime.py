from __future__ import annotations

import json
from dataclasses import dataclass

from .manifest import DEFAULT_MANIFEST, RuntimeManifest


@dataclass(frozen=True, slots=True)
class BootReport:
    runtime_name: str
    manifest: RuntimeManifest
    validated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "runtime_name": self.runtime_name,
            "validated": self.validated,
            "manifest": self.manifest.to_dict(),
        }


def boot_runtime(manifest: RuntimeManifest = DEFAULT_MANIFEST) -> BootReport:
    """Validate the manifest and return a boot report."""
    manifest.validate()
    return BootReport(runtime_name="trading-autopilot", manifest=manifest, validated=True)


def format_boot_report(report: BootReport) -> list[str]:
    lines = [
        f"runtime={report.runtime_name}",
        f"validated={str(report.validated).lower()}",
        f"schema_version={report.manifest.schema_version}",
    ]
    lines.extend(report.manifest.summary_lines())
    return lines


def main() -> None:
    report = boot_runtime()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
