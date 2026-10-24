"""Fork: what this deployment's cron adds on top of upstream.

* Redaction -- a chat delivery never discloses exception class names,
  traceback frames or filesystem paths (2026-07-28/29: an ImportError naming an
  absolute path reached a non-technical recipient's WhatsApp).
* Review-gate blocks are reported as such, not as a provider timeout.
* Audience -- who reads a job's deliveries ("operator" or "end_user"). An
  end-user job's technical failure is withheld from its chat and reported on
  the operator alert channel; the same marker rides into the turn so the
  finalizer drops engineering footers (agent/local_finalizer.py).
* Named private Telegram topics (``telegram:<chat>:<topic name>``) route by
  name through the DeliveryRouter, and native media follows the same topic.
* Role pin -- a job's ``role`` field becomes a ``[ROLE PIN: ...]`` line that
  overrides role routing; the job title is given to the router as metadata.

The bodies moved from the pre-v0.22.5 ``cron/scheduler.py`` and ``cron/jobs.py``;
upstream modules call in at small, marked call sites. Scheduler internals are
reached through the ``cron.scheduler`` facade at call time, never imported at
module load (the facade imports this module).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("cron.scheduler")


# ── job fields ────────────────────────────────────────────────────────────

# Role identifiers accepted for the per-job `role` pin (e.g. "scribe",
# "hermes_engineer_core"). The pin is rendered into the assembled prompt as
# a [ROLE PIN: ...] line, so the format must stay marker-safe.
_JOB_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Valid values for the per-job `audience` field. `end_user` marks a delivery
# target that is a non-technical reader -- a technical failure is withheld
# from that chat entirely and reported to the operator alert channel instead.
CRON_AUDIENCES = ("operator", "end_user")


def normalize_job_role(role: Optional[str]) -> Optional[str]:
    """Validate/normalize a job's role pin; raise ValueError on malformed input."""
    if role is None:
        return None
    normalized = str(role).strip().lower()
    if not normalized:
        return None
    if not _JOB_ROLE_RE.match(normalized):
        raise ValueError(
            f"Invalid cron job role {role!r}: must match [a-z][a-z0-9_]* (max 64 chars)"
        )
    return normalized


def normalize_audience(value) -> Optional[str]:
    """Normalize the `audience` field; None when unset (stored jobs keep no key)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in CRON_AUDIENCES:
        raise ValueError(f"audience must be one of {CRON_AUDIENCES}, got {value!r}")
    return text


# ── prompt ────────────────────────────────────────────────────────────────

def prefix_job_metadata(job: dict, prompt: str) -> str:
    """Give the role router the job's title: a job named "Вечерний прогноз погоды"
    must not be routed by whatever its injected script output happens to say."""
    job_title = str(job.get("name") or "").strip()
    if not job_title or str(job.get("prompt") or "").strip().startswith(job_title):
        return prompt
    return "## Scheduled Task Metadata\n" f"Title: {job_title}\n\n" f"{prompt}"


def prefix_role_pin(job: dict, prompt: str) -> str:
    """Deterministic role pin from the job's `role` field.

    Placed right after the cron banner (which routing strips from the very
    start of the text) so ``profile_request_context.extract_role_pin()`` finds
    it and role selection bypasses both the LLM router and the keyword cascade
    -- injected script output can no longer poison role choice.
    """
    job_role = str(job.get("role") or "").strip()
    if not job_role:
        return prompt
    return f"[ROLE PIN: {job_role}]\n\n" + prompt


# ── failure text ──────────────────────────────────────────────────────────

# Any dotted exception-like class name used as a wrapper, e.g. "ImportError: ",
# "httpx.HTTPStatusError: ". Not anchored to the start of the text: a wrapper
# commonly trails other prose ("Script execution failed: PermissionError: ...")
# or follows "During handling of the above exception..." in a chained
# traceback. Case-insensitive so lowercase names (socket.gaierror) are caught;
# the lookbehind keeps a match from starting mid-identifier. One global
# substitution also handles chained wrappers.
_EXC_WRAPPER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Interrupt):\s*",
    re.IGNORECASE,
)
# URLs are exempt from path redaction: a failing provider URL is the most
# useful diagnostic token in an operator alert and discloses no install layout.
_URL_RE = re.compile(r"https?://[^\s'\"()]+", re.IGNORECASE)
# Filesystem paths: Windows drive paths; absolute POSIX paths with 2+ segments;
# relative paths with 3+ segments; and two-segment repository paths whose last
# segment has an extension (config/settings.yaml). Two generic segments such as
# ``source/hash429abc.md`` are identifiers as often as locations, so that case
# is limited to known repository roots.
_FS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\s'\"()]*"
    r"|(?:/[\w.\-]+){2,}/?"
    r"|[\w\-]+(?:/[\w\-]+){2,}"
    r"|(?:agent|cron|gateway|hermes_cli|job_intel|plugins|tests|tools|config)/[\w\-]+\.[A-Za-z0-9]{1,6}\b)"
)
# Combined pass: a URL is tried first and kept verbatim, so _FS_PATH_RE never
# chews into scheme://host/path.
_URL_OR_FS_PATH_RE = re.compile(
    r"(?P<url>" + _URL_RE.pattern + r")|(?P<path>" + _FS_PATH_RE.pattern + r")"
)
# Traceback frame lines -- dropped wholesale. Matches the header, "File ..."
# lines and caret markers; the indented source line printed under each
# "File ..." line is dropped statefully below.
_TRACEBACK_LINE_RE = re.compile(
    r"^\s*(?:Traceback \(most recent call last\):|File \".*|\s+\^+\s*)$"
)
_TRACEBACK_FILE_LINE_RE = re.compile(r'^\s*File "')


def _redact_url_or_path(match: "re.Match[str]") -> str:
    if match.group("url"):
        return match.group("url")
    return "…"


def redact_technical_detail(text: str) -> str:
    """Strip exception wrappers, traceback frames and filesystem paths."""
    lines = []
    drop_next_if_indented = False
    for line in text.splitlines():
        if _TRACEBACK_LINE_RE.match(line):
            drop_next_if_indented = bool(_TRACEBACK_FILE_LINE_RE.match(line))
            continue
        if drop_next_if_indented:
            drop_next_if_indented = False
            if line[:1].isspace():
                continue
        lines.append(line)
    cleaned = " ".join(lines)
    cleaned = _EXC_WRAPPER_RE.sub("", cleaned)
    cleaned = _URL_OR_FS_PATH_RE.sub(_redact_url_or_path, cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def review_gate_block_notice(job_name: str, text: str) -> Optional[str]:
    """The notice for a run the review gate blocked, or None.

    A review-gate block is an agent-workflow outcome, not a provider failure.
    It must be classified before the provider heuristics: the block report is
    large and routinely quotes logs containing "timeout", which mislabeled a
    block as a provider timeout (2026-07-16 rebase cron incident).
    """
    if not ("Review gate mode:" in text or (text.startswith("Hermes role:") and "\nReviewer:" in text)):
        return None
    verdict_match = re.search(r"^Reviewer:.*/\s*(\S+)\s*$", text, re.MULTILINE)
    verdict = verdict_match.group(1) if verdict_match else "blocked"
    approval = (
        " and explicit approval is required"
        if re.search(r"^Approval:\s*required\s*$", text, re.MULTILINE)
        else ""
    )
    return (
        f"⚠️ Cron '{job_name}' failed: review gate blocked completion "
        f"(reviewer verdict: {verdict}{approval}). "
        "Full details saved in cron output."
    )


# ── audience ──────────────────────────────────────────────────────────────

#: Context variable marking a job's turn (`gateway/session_context.py`, read
#: in `hermes_cli/run_evidence.py`).
CRON_AUDIENCE_CONTEXT_VAR = "HERMES_CRON_AUDIENCE"


def resolve_cron_audience(job: dict, cfg: Optional[dict] = None) -> str:
    """Who reads this job's deliveries: "operator" or "end_user". Never raises.

    Runs inside failure handling, where a second exception would compound the
    incident, so a hand-edited typo in ``jobs.json`` (``"enduser"``) degrades
    to the config-based check instead of raising like ``normalize_audience``.

    An explicit ``audience`` field wins. Otherwise any resolved delivery target
    listed in ``cron.end_user_targets`` marks the job as end-user facing -- a
    safety net for a job created later without the flag. The two silent
    degradations (unrecognized audience, malformed end_user_targets) are logged.
    """
    try:
        raw_audience = job.get("audience")
        explicit = str(raw_audience or "").strip().lower()
        if explicit in CRON_AUDIENCES:
            return explicit
        if explicit:
            logger.warning(
                "cron job %r has unrecognized audience %r; ignoring it and "
                "falling back to the cron.end_user_targets net (then operator)",
                job.get("id") or job.get("name") or "?", raw_audience,
            )

        cron_cfg = (cfg or {}).get("cron") if isinstance(cfg, dict) else None
        configured = (cron_cfg if isinstance(cron_cfg, dict) else {}).get("end_user_targets") or []
        # A hand-edited config.yaml easily drops the list dash and leaves a bare
        # string; iterating it would match nothing and silently disable the net.
        if isinstance(configured, str):
            configured = [configured]
        elif not isinstance(configured, (list, tuple, set, frozenset)):
            if configured:
                logger.warning(
                    "cron.end_user_targets is not a string or list (got %r); "
                    "ignoring it — the end-user safety net is disabled until this is fixed",
                    configured,
                )
            configured = []
        if not configured:
            return "operator"
        marked = {str(t).strip().lower() for t in configured if str(t).strip()}
        from cron import scheduler as _sched

        for target in _sched._resolve_delivery_targets(job):
            platform = str(target.get("platform", "")).lower()
            chat_id = str(target.get("chat_id", ""))
            if f"{platform}:{chat_id}".lower() in marked or chat_id.lower() in marked:
                return "end_user"
        return "operator"
    except Exception as exc:  # noqa: BLE001 — never raises, by contract
        logger.warning("cron audience resolution failed for job %r: %s", job.get("id"), exc)
        return "operator"


def _set_cron_audience_context(value: str) -> None:
    """Write the current job's audience into the turn context. Never raises."""
    try:
        from gateway.session_context import _VAR_MAP

        _VAR_MAP[CRON_AUDIENCE_CONTEXT_VAR].set(str(value or ""))
    except Exception as exc:  # noqa: BLE001 — a display marker must not fail a run
        logger.debug("failed to bind cron audience context: %s", exc)


def bind_cron_audience_context(job: dict, cfg: Optional[dict] = None) -> str:
    """Mark the job's turn with who will read its result.

    A cron turn runs on no platform while its result goes to ``deliver:``; the
    end of the turn sees only the former, so engineering footers reached end
    users past the platform gate (2026-08-01, job 150d115fe905: an evening
    weather forecast arrived with "where checks ran"). Passed as a string
    through the session context so run_evidence never imports the scheduler.
    Must be bound before the agent's worker thread copies the context, and
    cleared in run_job's ``finally``. Returns the bound value ("" on failure,
    which everywhere means "do not suppress").
    """
    try:
        if cfg is None:
            from cron import scheduler as _sched

            try:
                cfg = _sched.load_config()
            except Exception:  # noqa: BLE001 — no config just means no end_user_targets net
                cfg = None
        audience = str(resolve_cron_audience(job, cfg) or "")
    except Exception as exc:  # noqa: BLE001 — resolve never raises; belt and braces
        logger.debug("failed to resolve cron audience: %s", exc)
        audience = ""
    _set_cron_audience_context(audience)
    return audience


def clear_cron_audience_context() -> None:
    """Drop the audience mark when the job ends, so it cannot leak into the next job."""
    _set_cron_audience_context("")


def plan_cron_failure_delivery(
    job: dict, error: Optional[str], cfg: Optional[dict] = None
) -> tuple[Optional[str], Optional[str]]:
    """Decide what a failed run delivers, and to whom.

    Returns ``(chat_text, operator_alert_text)``. ``chat_text is None`` means
    the job's own target gets nothing -- the policy for ``end_user`` jobs.
    ``operator_alert_text is None`` means the chat target already is the
    operator, so no separate alert is warranted.
    """
    from cron import scheduler as _sched

    if resolve_cron_audience(job, cfg) == "operator":
        return _sched._summarize_cron_failure_for_delivery(job, error), None

    job_name = job.get("name") or job.get("id") or "cron job"
    targets = ", ".join(
        f"{t.get('platform')}:{t.get('chat_id')}" for t in _sched._resolve_delivery_targets(job)
    ) or _sched._normalize_deliver_value(job.get("deliver", "local"))
    detail = redact_technical_detail(str(error or "unknown error")[:2000])
    if len(detail) > 600:
        detail = detail[:597].rstrip() + "..."
    alert = (
        f"⚠️ Cron '{job_name}' failed and the failure was WITHHELD from the "
        f"end-user target ({targets}) — nothing was delivered there.\n"
        f"Reason: {detail}\n"
        f"Job id: {job.get('id', '?')}. Full details saved in cron output."
    )
    return None, alert


def route_failure_for_audience(
    job: dict, *, success: bool, deliver_content: str, error: Optional[str]
) -> tuple[str, Optional[str]]:
    """Apply the audience policy to a composed failure notice.

    Returns ``(deliver_content, operator_alert)``; an empty ``deliver_content``
    means the job's chat gets nothing. Leaves the notice alone when the run
    succeeded, when upstream already decided to send nothing (an acked
    incident, a silent alert), and when the job routes failures through its own
    ``failure_deliver`` lane -- the operator chose that target explicitly.
    A failing policy falls back to delivering the notice, never to silence:
    withholding is only safe once ``end_user`` is positively resolved.
    """
    if success or not str(deliver_content or "").strip():
        return deliver_content, None
    if str(job.get("failure_deliver") or "").strip():
        return deliver_content, None
    try:
        from cron import scheduler as _sched

        try:
            cfg = _sched.load_config()
        except Exception:  # noqa: BLE001 — policy must not fail the run
            cfg = None
        if resolve_cron_audience(job, cfg) != "end_user":
            return deliver_content, None
        chat_text, alert = plan_cron_failure_delivery(job, error, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "plan_cron_failure_delivery failed for job %s, delivering the failure summary: %s",
            job.get("id"), exc,
        )
        return deliver_content, None
    if chat_text is None:
        try:
            alert += _sched._failure_streak_nudge(job)
        except Exception:  # noqa: BLE001 — the nudge is a courtesy
            pass
        return "", alert
    return deliver_content, None


def send_cron_operator_alert(
    alert_text: str, cfg: Optional[dict] = None, adapters=None, loop=None
) -> None:
    """Best-effort delivery of a withheld failure to the operator alert channel.

    Reuses ``gateway.error_alerts.channel`` (the channel turn-error alerts use)
    through a synthetic job, so target resolution, adapter reuse and the
    standalone fallback stay in ``_deliver_result``. Delivered unwrapped: the
    "Cronjob Response: <job>" header would name a job that does not exist.
    Never raises: an alert that cannot be delivered must not fail the run.
    """
    try:
        from cron import scheduler as _sched
        from gateway.turn_error_alerts import get_alert_config

        if cfg is None:
            cfg = _sched.load_config()
        alert_cfg = get_alert_config(cfg)
        if not alert_cfg:
            logger.warning(
                "cron operator alert dropped: gateway.error_alerts.channel is not configured (%s)",
                alert_text.splitlines()[0][:200] if alert_text else "",
            )
            return
        synthetic_job = {
            "id": "cron-operator-alert",
            "name": "cron operator alert",
            "deliver": alert_cfg["channel"],
            "origin": None,
        }
        _sched._deliver_result(synthetic_job, alert_text, adapters=adapters, loop=loop, wrap=False)
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        logger.warning("cron operator alert failed: %s", exc)


# ── Telegram topics and thread diagnostics ────────────────────────────────

def named_telegram_private_topic(platform: Any, chat_id: Any, thread_id: Any) -> Optional[str]:
    """The topic NAME when a target is ``telegram:<private chat>:<non-numeric topic>``."""
    from gateway.config import Platform
    from gateway.delivery import _looks_like_int, looks_like_telegram_private_chat_id

    if (
        platform == Platform.TELEGRAM
        and thread_id is not None
        and str(thread_id).strip()
        and looks_like_telegram_private_chat_id(str(chat_id))
        and not _looks_like_int(str(thread_id))
    ):
        return str(thread_id)
    return None


#: media_metadata key carrying a named topic to resolve right before the media send.
NAMED_TOPIC_MEDIA_KEY = "_hm_named_telegram_topic"


def resolve_named_telegram_dm_topic_id(
    runtime_adapter: Any, chat_id: Any, topic_name: str, loop: Any, job_id: str
) -> Optional[str]:
    """Numeric forum thread id for a named private Telegram topic, for native media.

    Text goes through ``DeliveryRouter``, which owns topic creation and stale
    mapping refresh. Native attachments bypass the router, so right before
    sending them we read the adapter's (normally cached) mapping again: media
    lands in the same ``message_thread_id`` the text send just resolved.
    """
    ensure_dm_topic = getattr(runtime_adapter, "ensure_dm_topic", None)
    if not callable(ensure_dm_topic):
        return None
    try:
        from agent.async_utils import safe_schedule_threadsafe

        future = safe_schedule_threadsafe(ensure_dm_topic(str(chat_id), topic_name), loop)
        if future is None:
            return None
        topic_id = future.result(timeout=10)
    except Exception:
        logger.warning(
            "Job '%s': failed to resolve named Telegram DM topic %r for media",
            job_id, topic_name, exc_info=True,
        )
        return None
    return str(topic_id) if topic_id else None


def _same_conversation(origin: dict, target: dict) -> bool:
    """True when *target* addresses the very conversation the job came from."""
    if not origin or not target:
        return False
    origin_platform = str(origin.get("platform") or "").strip().lower()
    target_platform = str(target.get("platform") or "").strip().lower()
    if not origin_platform or origin_platform != target_platform:
        return False
    origin_chat = str(origin.get("chat_id") or "").strip()
    target_chat = str(target.get("chat_id") or "").strip()
    return bool(origin_chat) and origin_chat == target_chat


def thread_id_dropped_for_origin_conversation(origin: dict, target: dict) -> bool:
    """True only when a same-conversation delivery silently lost its thread.

    Fan-out to another channel or platform legitimately has no origin thread:
    a Slack thread ts means nothing on Telegram. Comparing thread ids across
    conversations reported correct routing as breakage on every fire.
    """
    if not _same_conversation(origin, target):
        return False
    return bool(origin.get("thread_id")) and not target.get("thread_id")
