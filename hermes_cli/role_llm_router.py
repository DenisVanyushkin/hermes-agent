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
DEFAULT_ROLE_LLM_MODEL = "gpt-5.4-mini"
DEFAULT_ROLE_LLM_TIMEOUT_SECONDS = 8.0
DEFAULT_ROLE_LLM_MIN_CONFIDENCE = 0.7

_ROLE_DESCRIPTIONS = {
    "engineer": "code, repositories, debugging, tests, deployments, infrastructure, services, cron, logs diagnosis",
    "security_auditor": "explicit security reviews, audits of secrets/auth/exposure",
    "career_strategist": "vacancies, CV/resume, cover letters, interviews, career decisions, recruiter messaging",
    "artist": "drawing, generating or editing images/pictures/photos/logos/sketches/posters in any phrasing",
    "lawyer": "Kazakhstan law questions in any phrasing: laws, codes, articles, legal acts, rights and obligations, fines, contract legality, courts, налоги, трудовые споры",
    "scribe": "documentation, handoff notes, status capture, memory/state updates",
    "researcher": "external research, web fact-finding, summarizing sources",
    "general_operator": "ordinary personal/admin tasks: reminders, bookings, small questions, anything else",
}

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

    return RoleRoutingConfig(
        strategy=strategy,
        provider=str(section.get("provider") or DEFAULT_ROLE_LLM_PROVIDER),
        model=str(section.get("model") or DEFAULT_ROLE_LLM_MODEL),
        timeout_seconds=_num("timeout_seconds", DEFAULT_ROLE_LLM_TIMEOUT_SECONDS),
        min_confidence=_num("min_confidence", DEFAULT_ROLE_LLM_MIN_CONFIDENCE),
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
                "Report low confidence when genuinely unsure."
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


def select_role_via_llm(
    task: str,
    config: RoleRoutingConfig,
    *,
    llm_call: Callable[..., dict[str, Any]] | None = None,
) -> LLMRoleDecision | None:
    """Ask the LLM for a role. Returns None on ANY failure or low confidence.

    Callers must treat None as "use the deterministic cascade".
    """
    if not isinstance(task, str) or not task.strip():
        return None
    call = llm_call or _default_role_llm_call
    try:
        raw = call(
            provider=config.provider,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            messages=_build_messages(task),
        )
        role = str(raw.get("role") or "").strip()
        confidence = float(raw.get("confidence"))
        reasoning = str(raw.get("reasoning_summary") or "")
    except Exception as exc:  # noqa: BLE001 - fail soft to the cascade
        logger.warning("role LLM router failed, falling back to keyword cascade: %s", exc)
        return None
    if role not in SELECTABLE_ROLES:
        logger.warning("role LLM router returned unknown role %r; ignoring", role)
        return None
    if confidence < config.min_confidence:
        logger.info(
            "role LLM router confidence %.2f below threshold %.2f (role=%s); using cascade",
            confidence,
            config.min_confidence,
            role,
        )
        return None
    logger.info("ROLE_LLM_ROUTER_DECISION role=%s confidence=%.2f reason=%s", role, confidence, reasoning[:200])
    return LLMRoleDecision(role=role, confidence=confidence, reasoning_summary=reasoning)
