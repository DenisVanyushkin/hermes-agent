"""Build a frozen shortlist from one run or a completed Berlin calendar week.

The batch the owner reviews and the batch that is delivered must be the same
object, not two runs of the same query: the database moves twice a day, so
re-selecting at delivery time would send something the owner never saw. This
builder writes a canonical serialisation and records its SHA-256. The weekly
path retains the latest observation for each vacancy key and accounts for
every selected, sampled, suppressed and excluded key. Delivery re-computes
that digest and refuses to send on any mismatch.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any, Mapping
from zoneinfo import ZoneInfo

DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
DEFAULT_OUT = Path.home() / ".hermes" / "job_intel" / "shortlist"
# Both numbers are the owner's, answered explicitly on 2026-09-22: a wider
# batch loses fewer good roles to the cap, and three per employer keeps one
# company with seven openings from taking a quarter of the release while
# still showing genuinely distinct mandates.
BATCH_CAP = 25
MAX_PER_COMPANY = 3

_NORMALISE_SPLIT = re.compile(r"[^a-z0-9]+")
_TRAILING_REQUISITION = re.compile(r"\b\d{3,}\b")


def role_fit_evaluation_inputs(repo: Path) -> dict[str, Any]:
    """Pin the arguments the verdicts were computed with, not only the rule text.

    ruleset_version is a digest of role_fit.py, which is self-contained, so it
    is a faithful version of the *rules*. It is not a version of the
    *evaluation*: evaluate_role_fit takes owner_languages, supported_languages
    and short_contract_months as arguments, and a caller passing different
    ones produces different verdicts under an identical ruleset_version. The
    weekly verdicts are recomputed by this builder with module defaults, so
    the effective defaults plus the entrypoint make this artifact reproducible.
    """
    try:
        role_fit = _load_role_fit(repo / "job_intel" / "product_search" / "role_fit.py")
    except (ImportError, OSError) as exc:
        return {"available": False, "error": str(exc)}
    arguments = {
        "owner_languages": list(role_fit.DEFAULT_OWNER_LANGUAGES),
        "supported_languages": list(role_fit.DEFAULT_OWNER_LANGUAGES),
        "short_contract_months": role_fit.DEFAULT_SHORT_CONTRACT_MONTHS,
    }
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "available": True,
        "entrypoint": "scripts.job_intel_shortlist_build.build_weekly",
        "arguments": arguments,
        "arguments_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _load_role_fit(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_role_fit", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load role-fit rules from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def normalise_title(title: str) -> str:
    """Collapse a title to its identity, so one role in two cities is one role."""
    without_requisition = _TRAILING_REQUISITION.sub(" ", title.lower())
    return " ".join(part for part in _NORMALISE_SPLIT.split(without_requisition) if part)


def dedup_key(company: str, title: str) -> tuple[str, str]:
    return (" ".join(company.lower().split()), normalise_title(title))


def select_batch(rows: list[dict[str, Any]], cap: int = BATCH_CAP,
                 max_per_company: int = MAX_PER_COMPANY,
                 issued_keys: frozenset[str] = frozenset(),
                 order_key: Any = None,
                 ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Newest first, capped per company, hard cap, no filling with weaker roles.

    Ordering is total: first_seen_at descending, then company, title and
    vacancy_key, so the same run always produces the same batch in the same
    order. A shortage is delivered as a shortage; there is nothing in this
    function that can reach past the accepted set to reach the cap.

    Returns the batch and an audit of everything held back, grouped by the
    reason it was held back. A diversity policy that works by a bare
    `continue` is a policy nobody can review: a collapsed title and a role
    that was never a candidate look identical from the outside, and a false
    collapse becomes a loss the owner cannot notice. Naming the suppressed
    rows is what keeps that from being silent.

    Roles the owner's selection boundaries reject (company blacklist, crypto,
    Russia, scope, work authorisation...) are held back as `boundary_rejected`
    with their reasons. role_fit and the boundaries are separate rule systems
    and role_fit accepts roles the boundaries reject: run 511 delivered three
    roles of an explicitly blacklisted company. A row whose reasons were never
    recorded is held back as `boundary_unassessed` rather than treated as
    clean, because "no reasons recorded" and "no reasons found" are different
    facts. Both leave before the cap for the same reason issued roles do.

    `issued_keys` are vacancy keys already delivered in an earlier release.
    They are dropped before the cap is applied, not after: filtering
    afterwards would let roles the owner has already seen consume the cap and
    silently shrink the release. It arrives as a set rather than as a reader,
    so this function stays free of any knowledge of where releases are
    recorded, and its tests need no manifests on disk.
    """
    ordered = sorted(
        rows,
        key=order_key or (lambda row: (
            _descending(row.get("first_seen_at") or ""),
            row.get("company") or "",
            row.get("title") or "",
            row.get("vacancy_key") or "",
        )),
    )
    seen: dict[tuple[str, str], str] = {}
    per_company: dict[str, int] = {}
    batch: list[dict[str, Any]] = []
    suppressed: dict[str, list[dict[str, Any]]] = {
        "already_issued": [],
        "boundary_rejected": [],
        "boundary_unassessed": [],
        "title_collapsed": [],
        "company_capped": [],
        "over_cap": [],
    }

    def _record(reason: str, row: dict[str, Any], **extra: Any) -> None:
        suppressed[reason].append(
            {
                "vacancy_key": row.get("vacancy_key"),
                "company": row.get("company"),
                "title": row.get("title"),
                "location": row.get("location"),
                "url": row.get("url"),
                **extra,
            }
        )

    for row in ordered:
        company = " ".join((row.get("company") or "").lower().split())
        key = dedup_key(row.get("company") or "", row.get("title") or "")
        # The order of these branches is the definition of the groups: each row
        # leaves by the first one that matches, so the audit groups below are
        # disjoint by construction rather than by arithmetic. Changing the
        # order changes the partition.
        if row.get("vacancy_key") in issued_keys:
            _record("already_issued", row)
            continue
        reasons = row.get("selection_boundary_reasons")
        if reasons is None:
            _record("boundary_unassessed", row)
            continue
        if reasons:
            _record("boundary_rejected", row, reasons=list(reasons))
            continue
        if len(batch) >= cap:
            _record("over_cap", row)
            continue
        if key in seen:
            _record("title_collapsed", row, collapsed_into=seen[key])
            continue
        if per_company.get(company, 0) >= max_per_company:
            _record("company_capped", row, company_limit=max_per_company)
            continue
        seen[key] = str(row.get("vacancy_key") or "")
        per_company[company] = per_company.get(company, 0) + 1
        batch.append(row)
    return batch, suppressed


def _descending(value: str) -> tuple[int, str]:
    """Sort a timestamp string descending inside an otherwise ascending key."""
    return (0, "") if not value else (-1, _invert(value))


def _invert(value: str) -> str:
    return "".join(chr(0x10FFFD - ord(char)) if ord(char) < 0x10FFFD else char for char in value)


def parse_boundary_reasons(raw: str | None, vacancy_key: str) -> list[str] | None:
    """None means never assessed; anything unreadable stops the build.

    Reading a corrupt value as "no reasons" would deliver exactly the roles
    the boundaries exist to stop, so it is an error, not a default.
    """
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"selection_boundary_reasons_json of {vacancy_key} is not JSON: {raw!r}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError(f"selection_boundary_reasons_json of {vacancy_key} is not a list of strings: {raw!r}")
    return payload


def fetch_accepted(connection: sqlite3.Connection, run_id: int,
                   company_blacklist: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per accepted vacancy_key, with the boundary verdict of all its observations.

    A run can observe one vacancy_key several times under different URLs. The
    verdict is taken over every observation, never from one picked by SQL: a
    clean member must not mask a rejected one. Any rejection wins; otherwise
    any unassessed member makes the key unassessed.

    Reasons are frozen when a run is scored, so a company blacklisted after
    that would otherwise still travel. The blacklist in force at build time is
    applied on top, and recorded in the artifact by build().
    """
    rows = connection.execute(
        """
        SELECT o.vacancy_key, o.company, o.title, o.location, o.source,
               COALESCE(o.canonical_url, o.url) AS url,
               o.role_fit_rules_json, v.first_seen_at, v.last_seen_at, v.posted_at,
               o.selection_boundary_reasons_json
        FROM vacancy_observability AS o
        LEFT JOIN vacancies AS v ON v.vacancy_key = o.vacancy_key
        WHERE o.run_id = ? AND o.role_fit_verdict = 'accept'
        ORDER BY o.vacancy_key, url, o.selection_boundary_reasons_json
        """,
        (run_id,),
    ).fetchall()
    columns = [
        "vacancy_key", "company", "title", "location", "source", "url",
        "role_fit_rules_json", "first_seen_at", "last_seen_at", "posted_at",
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = dict(zip(columns, row[:-1]))
        reasons = parse_boundary_reasons(row[-1], record["vacancy_key"])
        # The blacklist is applied to each observation, before merging: one
        # key can carry different company labels, and the representative's
        # label must not decide whether a blacklisted one travels.
        company_key = canonical_company_key(record.get("company"))
        entry = company_blacklist.get(company_key) if company_key else None
        if entry is not None:
            current = ["company_blacklist", f"company_blacklist:{entry.get('origin') or 'unknown'}"]
            reasons = _merge_reasons(reasons, current)
        key = record["vacancy_key"]
        if key not in grouped:
            # The first member by URL is the representative; ORDER BY makes it deterministic.
            record["selection_boundary_reasons"] = reasons
            grouped[key] = record
            continue
        merged = grouped[key]["selection_boundary_reasons"]
        grouped[key]["selection_boundary_reasons"] = _merge_reasons(merged, reasons)
    return list(grouped.values())


def _merge_reasons(left: list[str] | None, right: list[str] | None) -> list[str] | None:
    """Rejection beats unassessed beats clean; reasons keep first-seen order."""
    if left or right:
        return list(dict.fromkeys([*(left or []), *(right or [])]))
    if left is None or right is None:
        return None
    return []


def load_company_blacklist(db_path: Path, repo: Path) -> dict[str, dict[str, Any]]:
    """The effective blacklist, computed by the same code the scoring path uses."""
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from job_intel import store

    # A job_intel imported earlier from another checkout would answer with
    # that checkout's blacklist file, silently. Refuse instead.
    loaded_from = Path(store.__file__).resolve()
    if not loaded_from.is_relative_to(repo.resolve()):
        raise RuntimeError(f"job_intel.store loaded from {loaded_from}, not from {repo}")
    return store.JobIntelStore(db_path).fetch_company_blacklist()


def canonical_company_key(value: str | None) -> str | None:
    """Mirror of job_intel.store.canonical_company_key; a test pins the two together."""
    raw = (value or "").strip()
    if not raw:
        return None
    collapsed = re.sub(r"\s+", " ", raw.casefold())
    if collapsed == "unknown":
        return "unknown"
    return "".join(ch for ch in collapsed if ch.isalnum()) or None


def ruleset_versions(rows: list[dict[str, Any]]) -> list[str]:
    versions: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row.get("role_fit_rules_json") or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ruleset_version"):
            versions.add(str(payload["ruleset_version"]))
    return sorted(versions)


def build(connection: sqlite3.Connection, run_id: int, commit: str,
          issued_keys: frozenset[str] = frozenset(),
          evaluation_inputs: dict[str, Any] | None = None,
          *, company_blacklist: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    accepted = fetch_accepted(connection, run_id, company_blacklist)
    batch, suppressed = select_batch(accepted, issued_keys=issued_keys)
    return {
        "artifact": "job_intel_shortlist",
        "version": "v1",
        "run_id": run_id,
        "commit": commit,
        "ruleset_versions": ruleset_versions(accepted),
        "role_fit_evaluation": evaluation_inputs if evaluation_inputs is not None else {"available": False},
        "built_at": datetime.now(timezone.utc).isoformat(),
        "cap": BATCH_CAP,
        "max_per_company": MAX_PER_COMPANY,
        "company_blacklist_applied": [
            {"company_key": key, "origin": str(company_blacklist[key].get("origin") or "unknown")}
            for key in sorted(company_blacklist)
        ],
        "accepted_total": len(accepted),
        "issued_keys_supplied": len(issued_keys),
        "suppressed_counts": {reason: len(rows) for reason, rows in sorted(suppressed.items())},
        "suppressed": {reason: rows for reason, rows in sorted(suppressed.items())},
        "delivered_count": len(batch),
        "short_of_cap": len(batch) < BATCH_CAP,
        "items": [
            {
                "position": index + 1,
                "vacancy_key": row["vacancy_key"],
                "company": row["company"],
                "title": row["title"],
                "location": row["location"],
                "source": row["source"],
                "url": row["url"],
                "first_seen_at": row["first_seen_at"],
                "posted_at": row["posted_at"],
            }
            for index, row in enumerate(batch)
        ],
    }


def weekly_bounds(week_start: date) -> tuple[datetime, datetime]:
    """Return the half-open Berlin Monday-to-Monday window in UTC."""
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday in Europe/Berlin")
    berlin = ZoneInfo("Europe/Berlin")
    start = datetime.combine(week_start, time.min, berlin)
    end = datetime.combine(week_start + timedelta(days=7), time.min, berlin)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _weekly_observations(connection: sqlite3.Connection, start: datetime,
                         end: datetime) -> tuple[list[dict[str, Any]], list[int]]:
    """One SELECT supplies the complete, consistent window and role text."""
    cursor = connection.execute(
        """
        SELECT o.run_id, o.vacancy_key, o.company, o.title, o.location,
               o.source, COALESCE(o.canonical_url, o.url) AS url, o.role_fit_verdict,
               o.role_fit_rules_json, o.selection_boundary_reasons_json,
               o.created_at, v.first_seen_at,
               v.posted_at, v.description
        FROM vacancy_observability AS o
        LEFT JOIN vacancies AS v ON v.vacancy_key = o.vacancy_key
        WHERE o.created_at >= ? AND o.created_at < ?
        """,
        (start.isoformat(), end.isoformat()),
    )
    columns = [column[0] for column in cursor.description]
    observations = [dict(zip(columns, row)) for row in cursor]
    for row in observations:
        observed = datetime.fromisoformat(row["created_at"])
        if observed.tzinfo is None or not start <= observed.astimezone(timezone.utc) < end:
            raise ValueError("observability timestamp is outside the frozen UTC window")
    return observations, sorted({int(row["run_id"]) for row in observations})


def _rule_payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(row.get("role_fit_rules_json") or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _frozen_item(row: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    payload = _rule_payload(row)
    role_fit_input = {key: row.get(key) or "" for key in ("title", "company", "location", "description")}
    input_bytes = json.dumps(role_fit_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    rule_ids = payload.get("rule_ids")
    posted, posted_unparsed = _posted_instant(row.get("posted_at"))
    posted_future = posted is not None and posted > as_of.astimezone(timezone.utc)
    if posted_future:
        posted = None
    return {
        "vacancy_key": row["vacancy_key"], "company": row["company"],
        "title": row["title"], "location": row["location"],
        "source": row["source"], "url": row["url"],
        "description": row["description"], "first_seen_at": row["first_seen_at"],
        "posted_at": row["posted_at"], "observed_at": row["created_at"],
        "posted_at_utc": posted.isoformat() if posted else None,
        "posted_at_unparsed": posted_unparsed,
        "posted_at_future": posted_future,
        "recency_source": "posted_at" if posted else "first_seen_at",
        "run_id": row["run_id"], "role_fit_verdict": row["role_fit_verdict"],
        "rule_ids": rule_ids if isinstance(rule_ids, list) else [],
        "ruleset_version": payload.get("ruleset_version"),
        "selection_boundary_reasons": row["selection_boundary_reasons"],
        # Observability does not store the exact description evaluated in each
        # run. This hash identifies the text read from vacancies at build time.
        "current_role_text_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "description_provenance": "vacancies_at_build",
    }


def _census_audit_item(row: dict[str, Any]) -> dict[str, Any]:
    """Keep every identity/verdict without copying every full description."""
    return {
        key: row[key] for key in (
            "vacancy_key", "company", "title", "source", "url", "run_id",
            "observed_at", "role_fit_verdict", "rule_ids", "ruleset_version",
            "current_role_text_sha256", "posted_at_utc", "posted_at_unparsed",
            "posted_at_future",
            "selection_boundary_reasons",
        )
    } | {
        "description_present": bool((row["description"] or "").strip()),
        "observed_role_fit_verdict": row.get("observed_role_fit_verdict"),
        "observed_rule_ids": row.get("observed_rule_ids"),
        "role_fit_error": row.get("role_fit_error"),
    }


def _utc_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid role date: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _posted_instant(value: str | None) -> tuple[datetime | None, bool]:
    """Parse observed scraper formats; unknown dates fall back visibly."""
    if not value:
        return None, False
    if re.fullmatch(r"\d{13}", value):
        try:
            return datetime.fromtimestamp(int(value) / 1000, timezone.utc), False
        except (OverflowError, OSError, ValueError):
            return None, True
    if value.endswith(" UTC"):
        value = value[:-4] + "+00:00"
    try:
        return _utc_instant(value), False
    except ValueError:
        return None, True


def _weekly_recency(row: dict[str, Any]) -> tuple[float, float, str]:
    first_seen = _utc_instant(row.get("first_seen_at")) or _utc_instant(row.get("observed_at"))
    if first_seen is None:
        raise ValueError("weekly role has neither first_seen_at nor observed_at")
    posted = _utc_instant(row.get("posted_at_utc")) or first_seen
    return (-posted.timestamp(), -first_seen.timestamp(), row["vacancy_key"])


def build_weekly(connection: sqlite3.Connection, week_start: date, commit: str,
                 *, release_id: str, issued_keys: frozenset[str] = frozenset(),
                 sample_cooldown_keys: frozenset[str] = frozenset(),
                 company_blacklist: Mapping[str, Mapping[str, Any]],
                 evaluation_inputs: dict[str, Any] | None = None,
                 as_of: datetime | None = None,
                 ruleset_path: Path | None = None,
                 _recompute: bool = True) -> dict[str, Any]:
    """Freeze the latest observation of every key in one seven-day window."""
    start, end = weekly_bounds(week_start)
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.astimezone(timezone.utc) < end:
        raise ValueError("weekly window must be complete before building")
    observations, run_ids = _weekly_observations(connection, start, end)
    latest: dict[str, dict[str, Any]] = {}
    boundary_by_key: dict[str, list[str] | None] = {}
    for row in observations:
        key = row["vacancy_key"]
        if not key:
            raise ValueError("observability row has no vacancy_key")
        reasons = parse_boundary_reasons(row["selection_boundary_reasons_json"], key)
        company_key = canonical_company_key(row.get("company"))
        entry = company_blacklist.get(company_key) if company_key else None
        if entry is not None:
            reasons = _merge_reasons(
                reasons, ["company_blacklist", f"company_blacklist:{entry.get('origin') or 'unknown'}"],
            )
        boundary_by_key[key] = _merge_reasons(boundary_by_key[key], reasons) if key in boundary_by_key else reasons
        previous = latest.get(key)
        order = (row["created_at"], row["run_id"], row["url"] or "")
        if previous is None or order > (previous["created_at"], previous["run_id"], previous["url"] or ""):
            latest[key] = row

    census = []
    for key in sorted(latest):
        row = dict(latest[key], selection_boundary_reasons=boundary_by_key[key])
        census.append(_frozen_item(row, as_of))
    if _recompute:
        role_fit = _load_role_fit(ruleset_path or Path(__file__).resolve().parents[1] / "job_intel" / "product_search" / "role_fit.py")
        for row in census:
            row["observed_role_fit_verdict"] = row["role_fit_verdict"]
            row["observed_rule_ids"] = row["rule_ids"]
            if not (row["description"] or "").strip():
                continue
            try:
                decision = role_fit.evaluate_role_fit(
                    row["title"] or "", row["company"] or "", row["location"] or "",
                    row["description"],
                )
            except Exception as error:
                row["role_fit_verdict"] = "error"
                row["rule_ids"] = []
                row["role_fit_error"] = type(error).__name__
            else:
                row["role_fit_verdict"] = decision.verdict
                row["rule_ids"] = list(decision.rule_ids)
            row["ruleset_version"] = role_fit.ROLE_FIT_RULESET_VERSION
    census_audit = [_census_audit_item(row) for row in census]
    accepted: list[dict[str, Any]] = []
    sample_candidates: list[dict[str, Any]] = []
    excluded: dict[str, list[dict[str, Any]]] = {
        "empty_description": [], "reject_not_sampled": [],
        "blocked_not_sampled": [], "not_evaluated": [], "sample_cooldown": [],
        "sample_boundary_rejected": [], "sample_boundary_unassessed": [],
    }
    for row in census:
        if not (row["description"] or "").strip():
            excluded["empty_description"].append(_census_audit_item(row))
        elif row["role_fit_verdict"] == "accept":
            accepted.append(row)
        elif row["role_fit_verdict"] in {"reject", "blocked"}:
            if row["selection_boundary_reasons"] is None:
                excluded["sample_boundary_unassessed"].append(_census_audit_item(row))
            elif row["selection_boundary_reasons"]:
                excluded["sample_boundary_rejected"].append(_census_audit_item(row))
            elif row["vacancy_key"] in sample_cooldown_keys:
                excluded["sample_cooldown"].append(_census_audit_item(row))
            else:
                sample_candidates.append(row)
        else:
            excluded["not_evaluated"].append(_census_audit_item(row))

    # Calibration's single-rule rejects and unresolved blocked roles are the
    # cheapest policy decisions to challenge. Sort within each class by a
    # release-bound hash so a retry produces exactly the same sample.
    def sample_order(row: dict[str, Any]) -> tuple[int, str, str]:
        priority = 0 if row["role_fit_verdict"] == "reject" and len(row["rule_ids"]) == 1 else 1 if row["role_fit_verdict"] == "blocked" else 2
        tie = hashlib.sha256(f"{release_id}:{row['vacancy_key']}".encode()).hexdigest()
        return priority, tie, row["vacancy_key"]

    sample = sorted(sample_candidates, key=sample_order)[:10]
    sampled_keys = {row["vacancy_key"] for row in sample}
    for row in sample_candidates:
        if row["vacancy_key"] not in sampled_keys:
            group = "reject_not_sampled" if row["role_fit_verdict"] == "reject" else "blocked_not_sampled"
            excluded[group].append(_census_audit_item(row))

    batch, suppressed = select_batch(
        accepted, issued_keys=issued_keys,
        order_key=_weekly_recency,
    )
    selected = [row | {"position": index + 1} for index, row in enumerate(batch)]
    by_key = {row["vacancy_key"]: row for row in census}
    suppressed = {
        reason: [entry | {
            "source": by_key[entry["vacancy_key"]]["source"],
            "first_seen_at": by_key[entry["vacancy_key"]]["first_seen_at"],
            "posted_at": by_key[entry["vacancy_key"]]["posted_at"],
            "posted_at_utc": by_key[entry["vacancy_key"]]["posted_at_utc"],
            "posted_at_unparsed": by_key[entry["vacancy_key"]]["posted_at_unparsed"],
            "posted_at_future": by_key[entry["vacancy_key"]]["posted_at_future"],
            "recency_source": by_key[entry["vacancy_key"]]["recency_source"],
        } for entry in entries]
        for reason, entries in suppressed.items()
    }
    partition = [row["vacancy_key"] for row in selected + sample]
    partition += [row["vacancy_key"] for group in suppressed.values() for row in group]
    partition += [row["vacancy_key"] for group in excluded.values() for row in group]
    if len(partition) != len(census) or set(partition) != set(latest):
        raise ValueError("weekly census partition is incomplete or overlapping")
    census_sha = hashlib.sha256(json.dumps(census_audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "artifact": "job_intel_weekly_shortlist", "version": "v2",
        "release_id": release_id, "week_start": week_start.isoformat(),
        "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
        "as_of": as_of.astimezone(timezone.utc).isoformat(), "built_at": datetime.now(timezone.utc).isoformat(),
        "run_ids": run_ids, "commit": commit,
        "company_blacklist_applied": [
            {"company_key": key, "origin": str(company_blacklist[key].get("origin") or "unknown")}
            for key in sorted(company_blacklist)
        ],
        "ruleset_versions": sorted({str(row["ruleset_version"]) for row in census if row.get("ruleset_version")}),
        "role_fit_evaluation": evaluation_inputs if evaluation_inputs is not None else {"available": False},
        "census_count": len(census), "census_sha256": census_sha,
        "census": census_audit,
        "partition_count": len(partition), "accepted_total": len(accepted),
        "issued_keys_supplied": len(issued_keys), "cap": BATCH_CAP,
        "max_per_company": MAX_PER_COMPANY, "delivered_count": len(selected),
        "short_of_cap": len(selected) < BATCH_CAP, "items": selected,
        "rejected_sample": sample, "suppressed": suppressed,
        "suppressed_counts": {key: len(value) for key, value in suppressed.items()},
        "excluded": excluded, "excluded_counts": {key: len(value) for key, value in excluded.items()},
    }


def canonical_bytes(artifact: dict[str, Any]) -> bytes:
    """Serialise for hashing: the digest must not depend on dict order or build time."""
    hashable = {key: value for key, value in artifact.items() if key != "built_at"}
    return json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(artifact)).hexdigest()


def _head_commit(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def render_text(artifact: dict[str, Any], sha: str) -> str:
    if artifact.get("version") == "v2":
        lines = [
            f"WEEKLY SHORTLIST {artifact['release_id']} {artifact['window_start_utc']}..{artifact['window_end_utc']}",
            f"census={artifact['census_count']} runs={len(artifact['run_ids'])} selected={artifact['delivered_count']}",
            f"rejected_sample={len(artifact['rejected_sample'])} excluded={artifact['excluded_counts']}",
            f"suppressed={artifact['suppressed_counts']}",
            f"census_sha256={artifact['census_sha256']}",
            f"sha256={sha}",
        ]
        for item in artifact["items"]:
            lines.append(f"{item['position']}. {item['company']} — {item['title']} | {item['url']}")
        return "\n".join(lines)
    lines = [
        f"SHORTLIST run_id={artifact['run_id']} delivered={artifact['delivered_count']} of cap {artifact['cap']}",
        f"accepted_total={artifact['accepted_total']} ruleset={','.join(artifact['ruleset_versions']) or 'unknown'}",
        f"commit={artifact['commit']}",
        f"eval_args={(artifact.get('role_fit_evaluation') or {}).get('arguments_sha256', 'unknown')[:12]}",
        f"sha256={sha}",
        "",
    ]
    for item in artifact["items"]:
        lines.append(f"{item['position']}. {item['company']} — {item['title']}")
        lines.append(f"   {item['location']} | {item['source']} | first seen {item['first_seen_at']}")
        lines.append(f"   {item['url']}")
    held = artifact.get("suppressed_counts") or {}
    if any(held.values()):
        lines += ["", "HELD BACK " + " ".join(f"{k}={v}" for k, v in sorted(held.items()) if v)]
        for reason, rows in sorted((artifact.get("suppressed") or {}).items()):
            for entry in rows[:5]:
                why = f" ({', '.join(entry['reasons'])})" if entry.get("reasons") else ""
                lines.append(f"   [{reason}] {entry['company']} — {entry['title']} | {entry['location']}{why}")
    if artifact["short_of_cap"]:
        lines += [
            "",
            f"Short of the cap: {artifact['delivered_count']} of {artifact['cap']}. The accepted set "
            "held nothing further that the rules above admit; see HELD BACK for what was not weaker "
            "but merely capped or collapsed. Nothing weaker was added to reach the cap.",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", type=int)
    source.add_argument("--week-start", type=date.fromisoformat, help="Monday date in Europe/Berlin")
    source.add_argument("--weekly", action="store_true", help="previous completed Berlin ISO week")
    parser.add_argument("--release-id")
    parser.add_argument("--repo", type=Path, default=Path.home() / ".hermes" / "hermes-agent")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument(
        "--issued-keys",
        type=Path,
        default=None,
        help="JSON array of vacancy keys already delivered; they are dropped before the cap",
    )
    args = parser.parse_args()

    if (args.week_start or args.weekly) and args.issued_keys is None:
        parser.error("weekly builds require --issued-keys from delivered release manifests")

    issued_keys: frozenset[str] = frozenset()
    if args.issued_keys is not None:
        payload = json.loads(args.issued_keys.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("--issued-keys must contain a JSON array of vacancy keys")
        issued_keys = frozenset(str(item) for item in payload)

    company_blacklist = load_company_blacklist(args.db, args.repo)
    connection = connect_read_only(args.db)
    if args.week_start or args.weekly:
        berlin_today = datetime.now(ZoneInfo("Europe/Berlin")).date()
        week_start = args.week_start or (berlin_today - timedelta(days=berlin_today.weekday() + 7))
        release_id = args.release_id or f"shortlist-{week_start.isocalendar().year}-W{week_start.isocalendar().week:02d}"
        artifact = build_weekly(
            connection, week_start, _head_commit(args.repo), release_id=release_id,
            issued_keys=issued_keys, evaluation_inputs=role_fit_evaluation_inputs(args.repo),
            ruleset_path=args.repo / "job_intel" / "product_search" / "role_fit.py",
            company_blacklist=company_blacklist,
        )
    else:
        artifact = build(
            connection, args.run_id, _head_commit(args.repo), issued_keys=issued_keys,
            evaluation_inputs=role_fit_evaluation_inputs(args.repo),
            company_blacklist=company_blacklist,
        )
    sha = digest(artifact)
    print(render_text(artifact, sha))
    if args.print_only:
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.out_dir / (artifact["release_id"] if args.week_start or args.weekly else f"{stamp}-run{args.run_id}")
    target.mkdir(parents=True, exist_ok=True)
    (target / "shortlist.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (target / "shortlist.txt").write_text(render_text(artifact, sha), encoding="utf-8")
    (target / "SHA256SUMS").write_text(f"{sha}  canonical\n", encoding="utf-8")
    print(f"\nartifact: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
