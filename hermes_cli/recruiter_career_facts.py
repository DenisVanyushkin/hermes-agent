"""Career facts source of truth for recruiter decision support.

The SoT lives outside the git repo at ``~/.hermes/job_intel/career_facts/``:

- ``career_facts.json`` — structured resume (candidate facts)
- ``preferences.yaml``  — selection criteria / preferences
- ``manifest.yaml``     — approval flag + sha256 of both files

Facts are loaded ONLY when the manifest is approved and every listed hash
matches the file on disk. On any mismatch the loader fails soft: it returns
no sources (modules degrade to "career facts unavailable") and a warning —
it never raises into the flow and never lets silently-edited facts through.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.yaml"
CAREER_FACTS_DIRNAME = "career_facts"


@dataclass(slots=True)
class CareerFactsBundle:
    sources: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] | None = None
    preferences: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.sources)


def career_facts_dir(hermes_home: Path | str | None = None) -> Path:
    if hermes_home is not None:
        home = Path(hermes_home)
    else:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "job_intel" / CAREER_FACTS_DIRNAME


def load_career_facts(hermes_home: Path | str | None = None) -> CareerFactsBundle:
    base = career_facts_dir(hermes_home)
    manifest_path = base / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return CareerFactsBundle(warnings=["career facts manifest not found"])

    try:
        import yaml

        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return CareerFactsBundle(warnings=[f"career facts manifest unreadable: {type(exc).__name__}"])

    if not bool(manifest.get("approved")):
        return CareerFactsBundle(warnings=["career facts manifest not approved"])

    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        return CareerFactsBundle(warnings=["career facts manifest lists no files"])

    bundle = CareerFactsBundle()
    verified: dict[str, Path] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return CareerFactsBundle(warnings=["career facts manifest entry malformed"])
        rel_path = str(entry.get("path") or "")
        expected = str(entry.get("sha256") or "").lower()
        target = (base / rel_path).resolve()
        if not rel_path or not expected or base.resolve() not in target.parents:
            return CareerFactsBundle(warnings=[f"career facts manifest entry invalid: {rel_path or '<empty>'}"])
        if not target.is_file():
            return CareerFactsBundle(warnings=[f"career facts file missing: {rel_path}"])
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            # Integrity failure disables the whole bundle: partial trust is no trust.
            return CareerFactsBundle(
                warnings=[f"career facts integrity check failed for {rel_path}; facts not loaded"]
            )
        verified[rel_path] = target
        bundle.sources.append(
            {
                "source_id": rel_path,
                "source_kind": str(entry.get("source_kind") or "structured_resume"),
                "source_type": str(entry.get("source_type") or "structured_resume"),
                "approved": True,
            }
        )

    for rel_path, target in verified.items():
        try:
            if target.suffix == ".json":
                payload = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and bundle.facts is None:
                    bundle.facts = payload
            elif target.suffix in {".yaml", ".yml"}:
                import yaml

                payload = yaml.safe_load(target.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and bundle.preferences is None:
                    bundle.preferences = payload
        except Exception as exc:
            bundle.warnings.append(f"career facts file unparseable: {rel_path} ({type(exc).__name__})")

    return bundle
