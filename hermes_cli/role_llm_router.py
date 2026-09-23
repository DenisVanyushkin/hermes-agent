"""LLM-based role selection for the Hermes profile architecture.

Sits in front of the deterministic keyword cascade in
``hermes_cli.profile_execution._select_role``. When ``role_routing.strategy``
is ``llm`` in config.yaml, ``build_role_context_for_task`` asks a small LLM to
classify the task into one of the built-in roles; the keyword cascade remains
the authoritative fallback whenever the LLM call fails, times out, returns an
unknown role, or is not confident enough.

Kept import-light like ``pipeline_router``: the OpenAI-compatible client is
resolved lazily inside the default call hook so unit tests can inject a fake.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Roles the LLM may select. chief_hermes is a coordinator, not a task role.
SELECTABLE_ROLES: tuple[str, ...] = (
    "engineer",
    "security_auditor",
    "career_strategist",
    "artist",
    "lawyer",
    "scribe",
    "researcher",
    "general_operator",
)

DEFAULT_ROLE_ROUTING_STRATEGY = "deterministic"
DEFAULT_ROLE_LLM_PROVIDER = "openai-codex"
DEFAULT_ROLE_LLM_MODEL = "gpt-6-luna"
DEFAULT_ROLE_LLM_TIMEOUT_SECONDS = 8.0
DEFAULT_ROLE_LLM_MIN_CONFIDENCE = 0.7

DEFAULT_JEV_MODEL = "~typesafe/jev-latest"
DEFAULT_JEV_TIMEOUT_SECONDS = 4.0
JEV_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

# Explicit boundaries, measured 2026-09-23 on a blind-labelled holdout of 120
# real operator requests (tuned on a separate dev split): the previous
# one-line descriptions, whose general_operator ended in "anything else",
# gave 105/120 with 11 confident misroutes; these give 116/120 with 3 on
# the chat router and 113/120 with none on Jev. The same text feeds both.
_ROLE_DESCRIPTIONS = {
    "engineer": (
        "Change, fix, build, configure, deploy, review or diagnose software or infrastructure: code, "
        "git/commits, tests, servers, containers, logs, and Hermes itself (its skills, cron jobs, "
        "gateway, channels/integrations, models, config, footer, notifications, delivery). Includes "
        "'why didn't you reply / did you receive it' about Hermes delivery, pasted terminal output or "
        "errors, requests for exact commands, approving or continuing an implementation plan, and "
        "planning a new Hermes feature or automation, including setting up or changing recurring jobs "
        "such as daily digests, channel summaries, idea generation or scheduled messages. Ordinary code "
        "review is engineer."
    ),
    "security_auditor": (
        "Only when the user explicitly asks whether something is safe or trustworthy, or for a "
        "security audit: vulnerabilities, secrets/credentials handling, auth and access control, public "
        "exposure, trust in third-party code or skills. Bug diagnosis or general code review is engineer."
    ),
    "career_strategist": (
        "Job search and career: vacancies, whether to apply, CV/resume, cover letters, interviews, "
        "recruiters, career moves, even when phrased as a comparison."
    ),
    "artist": "Create or edit images, pictures, avatars, logos, posters, stickers, or visual style variants.",
    "lawyer": (
        "Legal questions: what a law, code or article says, rights and obligations, fines, legality of "
        "a contract or action, courts, taxes, labor law."
    ),
    "scribe": (
        "Write or update durable text records without building anything: add an idea or task to the "
        "backlog, read back the backlog, write docs, runbooks, requirement specs (БФТ/PRD), handoff or "
        "final status notes, a summary of a conversation, persistent rules or memory entries."
    ),
    "researcher": (
        "Find, compare or explain outside information: web research, products and devices (e.g. which "
        "UPS to buy, runtime estimates), technologies, news, what an external tool or skill does. No "
        "request to change code or Hermes, and not setting up a recurring job."
    ),
    "general_operator": (
        "Personal life admin: calendar events, reminders, messages to other people, weather, bookings, "
        "fitness schedule, household questions, small talk. Use when the request is personal rather than "
        "technical and no other role clearly fits."
    ),
}

_ROUTING_INSTRUCTIONS = (
    "Which Hermes role should handle the user's latest message? The text may start with "
    "'[Replying to: \"...\"]' quoting an earlier message: classify the user's new message, using the "
    "quote only as context for what 'this'/'it' refers to. Judge intent, not keywords. Short follow-ups "
    "(continue, commit, approved, send it) inherit the role of the task they continue."
)

_RESPONSE_FORMAT = {
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "role_router_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {"type": "string", "enum": list(SELECTABLE_ROLES)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasoning_summary": {"type": "string"},
                },
                "required": ["role", "confidence", "reasoning_summary"],
            },
        },
    }
}


@dataclass(frozen=True)
class RoleRoutingConfig:
    strategy: str = DEFAULT_ROLE_ROUTING_STRATEGY
    provider: str = DEFAULT_ROLE_LLM_PROVIDER
    model: str = DEFAULT_ROLE_LLM_MODEL
    timeout_seconds: float = DEFAULT_ROLE_LLM_TIMEOUT_SECONDS
    min_confidence: float = DEFAULT_ROLE_LLM_MIN_CONFIDENCE
    # Optional first tier: Jev on OpenRouter's typed Decisions API. Fast
    # (~0.35s) and independent of Codex quota; when it is not confident the
    # chat router below still decides, so enabling it can only add a tier.
    jev_enabled: bool = False
    jev_model: str = DEFAULT_JEV_MODEL
    jev_timeout_seconds: float = DEFAULT_JEV_TIMEOUT_SECONDS
    jev_min_confidence: float = DEFAULT_ROLE_LLM_MIN_CONFIDENCE


@dataclass(frozen=True)
class LLMRoleDecision:
    role: str
    confidence: float
    reasoning_summary: str = ""


def load_role_routing_config(config: dict[str, Any] | None) -> RoleRoutingConfig:
    """Parse the ``role_routing`` section of config.yaml (defaults on any junk)."""
    section = (config or {}).get("role_routing")
    if not isinstance(section, dict):
        return RoleRoutingConfig()
    strategy = str(section.get("strategy") or DEFAULT_ROLE_ROUTING_STRATEGY).strip().lower()
    if strategy not in {"deterministic", "llm"}:
        strategy = DEFAULT_ROLE_ROUTING_STRATEGY

    def _num(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    jev = section.get("jev") if isinstance(section.get("jev"), dict) else {}

    def _jev_num(key: str, default: float) -> float:
        try:
            return float(jev.get(key, default))
        except (TypeError, ValueError):
            return default

    return RoleRoutingConfig(
        strategy=strategy,
        provider=str(section.get("provider") or DEFAULT_ROLE_LLM_PROVIDER),
        model=str(section.get("model") or DEFAULT_ROLE_LLM_MODEL),
        timeout_seconds=_num("timeout_seconds", DEFAULT_ROLE_LLM_TIMEOUT_SECONDS),
        min_confidence=_num("min_confidence", DEFAULT_ROLE_LLM_MIN_CONFIDENCE),
        # Only a real YAML boolean enables it: "yes" as a string stays off.
        jev_enabled=jev.get("enabled") is True,
        jev_model=str(jev.get("model") or DEFAULT_JEV_MODEL),
        jev_timeout_seconds=_jev_num("timeout_seconds", DEFAULT_JEV_TIMEOUT_SECONDS),
        jev_min_confidence=_jev_num("min_confidence", DEFAULT_ROLE_LLM_MIN_CONFIDENCE),
    )


def _build_messages(task: str) -> list[dict[str, str]]:
    role_lines = "\n".join(f"- {role}: {desc}" for role, desc in _ROLE_DESCRIPTIONS.items())
    return [
        {
            "role": "system",
            "content": (
                "You are the Hermes role router. Classify the user's task into exactly "
                "one built-in role and reply with ONLY a JSON object matching the schema "
                '{"role": <enum>, "confidence": <0..1>, "reasoning_summary": <string>}.\n\n'
                f"Roles:\n{role_lines}\n\n"
                "Rules: judge intent, not keywords — paraphrases and typos in any language "
                "must still map to the right role. Image creation/editing in ANY phrasing "
                "(draw, нарисуй, изобрази, сделай в стиле..., make me a wallpaper) is artist. "
                "Questions about legal rights, obligations, legality, fines or what the law "
                "says (закон, кодекс, статья, договор, штраф) in ANY phrasing are lawyer; "
                "job search, resumes and vacancies are career_strategist even when labor "
                "topics overlap. "
                "Report low confidence when genuinely unsure. "
                f"{_ROUTING_INSTRUCTIONS}"
            ),
        },
        {
            "role": "user",
            "content": (
                "Examples:\n"
                'Input: "изобрази-ка мне закат как у Миядзаки" -> {"role": "artist", "confidence": 0.95, "reasoning_summary": "image request"}\n'
                'Input: "почини падающий тест в CI" -> {"role": "engineer", "confidence": 0.95, "reasoning_summary": "code fix"}\n'
                'Input: "стоит ли откликаться на эту вакансию" -> {"role": "career_strategist", "confidence": 0.9, "reasoning_summary": "vacancy decision"}\n'
                'Input: "Могут ли меня уволить, пока я на больничном?" -> {"role": "lawyer", "confidence": 0.95, "reasoning_summary": "legal rights question"}\n'
                'Input: "напомни завтра про стоматолога" -> {"role": "general_operator", "confidence": 0.9, "reasoning_summary": "personal reminder"}\n\n'
                f"Task:\n{task.strip()}\n"
            ),
        },
    ]


def _default_role_llm_call(
    *,
    provider: str,
    model: str,
    timeout_seconds: float,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    from agent.auxiliary_client import extract_content_or_reasoning, resolve_provider_client

    client, resolved_model = resolve_provider_client(provider, model)
    if client is None:
        raise RuntimeError(f"No client available for role router provider={provider!r}")
    response = client.chat.completions.create(
        model=resolved_model or model,
        messages=messages,
        timeout=timeout_seconds,
        extra_body=json.loads(json.dumps(_RESPONSE_FORMAT)),
    )
    raw_text = extract_content_or_reasoning(response).strip()
    if not raw_text:
        raise RuntimeError("Role router LLM returned an empty response body")
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Role router LLM returned non-object JSON: {type(parsed).__name__}")
    return parsed


def _default_jev_call(*, model: str, timeout_seconds: float, task: str) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = {
        "model": model,
        "state": task,
        "questions": {
            "role": {
                "type": "choice",
                "instructions": _ROUTING_INSTRUCTIONS,
                "criteria": dict(_ROLE_DESCRIPTIONS),
            }
        },
    }
    request = urllib.request.Request(
        JEV_DECISIONS_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = json.loads(response.read())
    answer = ((raw or {}).get("answers") or {}).get("role") or {}
    return {"role": answer.get("choice"), "confidence": answer.get("confidence")}


def _usable(role: Any, confidence: Any, threshold: float, source: str) -> tuple[str, float] | None:
    role = str(role or "").strip()
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        logger.warning("role %s router returned non-numeric confidence; ignoring", source)
        return None
    if not math.isfinite(confidence):
        logger.warning("role %s router returned non-finite confidence; ignoring", source)
        return None
    if role not in SELECTABLE_ROLES:
        logger.warning("role %s router returned unknown role %r; ignoring", source, role)
        return None
    if confidence < threshold:
        logger.info(
            "role %s router confidence %.2f below threshold %.2f (role=%s); falling through",
            source,
            confidence,
            threshold,
            role,
        )
        return None
    return role, confidence


def _select_role_via_jev(task: str, config: RoleRoutingConfig, call: Callable[..., dict[str, Any]]) -> LLMRoleDecision | None:
    try:
        raw = call(model=config.jev_model, timeout_seconds=config.jev_timeout_seconds, task=task.strip())
        usable = _usable(raw.get("role"), raw.get("confidence"), config.jev_min_confidence, "Jev")
    except Exception as exc:  # noqa: BLE001 - fail soft to the chat router
        logger.warning("role Jev router failed, falling back to chat router: %s", exc)
        return None
    if usable is None:
        return None
    role, confidence = usable
    logger.info("ROLE_LLM_ROUTER_DECISION role=%s confidence=%.2f reason=jev", role, confidence)
    return LLMRoleDecision(role=role, confidence=confidence, reasoning_summary="jev")


def select_role_via_llm(
    task: str,
    config: RoleRoutingConfig,
    *,
    llm_call: Callable[..., dict[str, Any]] | None = None,
    jev_call: Callable[..., dict[str, Any]] | None = None,
) -> LLMRoleDecision | None:
    """Ask the LLM for a role. Returns None on ANY failure or low confidence.

    With Jev enabled it is asked first; the chat router runs only when Jev
    fails or is not confident. Callers must treat None as "use the
    deterministic cascade".
    """
    if not isinstance(task, str) or not task.strip():
        return None
    if config.jev_enabled:
        decision = _select_role_via_jev(task, config, jev_call or _default_jev_call)
        if decision is not None:
            return decision
    call = llm_call or _default_role_llm_call
    try:
        raw = call(
            provider=config.provider,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            messages=_build_messages(task),
        )
        reasoning = str(raw.get("reasoning_summary") or "")
        usable = _usable(raw.get("role"), raw.get("confidence"), config.min_confidence, "LLM")
    except Exception as exc:  # noqa: BLE001 - fail soft to the cascade
        logger.warning("role LLM router failed, falling back to keyword cascade: %s", exc)
        return None
    if usable is None:
        return None
    role, confidence = usable
    logger.info("ROLE_LLM_ROUTER_DECISION role=%s confidence=%.2f reason=%s", role, confidence, reasoning[:200])
    return LLMRoleDecision(role=role, confidence=confidence, reasoning_summary=reasoning)
