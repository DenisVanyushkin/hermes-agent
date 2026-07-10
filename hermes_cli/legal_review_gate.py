"""Legal answer review gate: deterministic citation verification (stage 1)
and pinned-model LLM verdict (stage 2) for the lawyer role.

Stage 1 never uses an LLM: it re-fetches every cited act/article from
adilet.zan.kz and mechanically verifies existence, status, quote fidelity
and claimed inter-act links. Network failures degrade to "unverifiable",
never to "refuted".

Stage 2 sends the draft answer plus stage-1 evidence to a config-pinned
review model (tier ``legal_review`` in config/hermes-model-policy.yaml) and
parses a strict-JSON verdict. Hard finding TYPES force ``changes_requested``
regardless of severity; a failed review call fails OPEN with disclosure
(``review_unavailable``), never silently.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path

from tools.legal_research.adilet_client import AdiletClient, AdiletError, AdiletNetworkError

QUOTE_MATCH_THRESHOLD = 0.8
_REPEALED_MARKERS = ("утратил силу", "утратившим силу", "утратило силу", "отменен")

HARD_FINDING_TYPES = frozenset({
    "nonexistent_article", "misquoted_norm", "wrong_act_status",
    "unsupported_inter_act_link", "nonexistent_act",
})
_FINDING_TYPES = (
    "nonexistent_article", "misquoted_norm", "wrong_act_status",
    "unsupported_inter_act_link", "inference_presented_as_norm",
    "missing_limitation", "answer_beyond_sources",
)
_REVIEW_TIER = "legal_review"
_REPORT_DIR = Path.home() / ".hermes" / "cache" / "legal_qa"

_REVIEW_SCHEMA = {
    "verdict": "approved | changes_requested",
    "findings": [{
        "type": " | ".join(_FINDING_TYPES),
        "severity": "high | medium | low",
        "quote": "the exact answer passage at fault",
        "explanation": "why this is wrong, referencing the evidence",
        "suggested_fix": "concrete replacement or 'remove'",
    }],
    "summary": "one-paragraph review summary in Russian",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _quote_ratio(quote: str, body: str) -> float:
    """Best fuzzy-match ratio of the quote against a sliding window of the body."""
    nq, nb = _normalize(quote), _normalize(body)
    if not nq or not nb:
        return 0.0
    if nq in nb:
        return 1.0
    window = max(len(nq) * 3, 300)
    step = max(len(nq) // 2, 50)
    best = 0.0
    for start in range(0, max(len(nb) - len(nq), 1), step):
        chunk = nb[start:start + window]
        ratio = difflib.SequenceMatcher(None, nq, chunk).ratio()
        best = max(best, ratio)
        if best >= 0.98:
            break
    return best


def verify_citations(citations: list[dict], client: AdiletClient | None = None) -> list[dict]:
    """Deterministically verify each citation against the live source.

    Returns one evidence dict per citation; ``checks_failed`` codes:
    nonexistent_act, nonexistent_article, misquoted_norm, wrong_act_status,
    unsupported_inter_act_link. Network problems land in ``unverifiable``.
    """
    client = client or AdiletClient()
    info_cache: dict[str, dict] = {}
    text_cache: dict[tuple[str, str | None], dict] = {}
    links_cache: dict[str, dict] = {}
    results = []
    for citation in citations or []:
        results.append(_verify_one(citation, client, info_cache, text_cache, links_cache))
    return results


def _verify_one(citation, client, info_cache, text_cache, links_cache) -> dict:
    doc_id = str(citation.get("doc_id") or "").strip()
    raw_article = citation.get("article")
    article = str(raw_article).strip() if raw_article not in (None, "") else None
    quote = citation.get("quote") or None
    raw_linked = citation.get("linked_doc_id")
    linked = str(raw_linked).strip() if raw_linked not in (None, "") else None

    evidence = {
        "citation": citation,
        "act_exists": False,
        "act_status": None,
        "act_repealed": False,
        "article_found": None,
        "quote_match_ratio": None,
        "quote_verified": None,
        "link_verified": None,
        "checks_failed": [],
        "unverifiable": [],
    }
    if not doc_id:
        evidence["unverifiable"].append("citation_missing_doc_id")
        return evidence

    try:
        if doc_id not in info_cache:
            info_cache[doc_id] = client.get_act_info(doc_id)
        info = info_cache[doc_id]
        evidence["act_exists"] = True
        evidence["act_status"] = info.get("status")
        status_norm = _normalize(info.get("status") or "")
        if any(marker in status_norm for marker in _REPEALED_MARKERS):
            evidence["act_repealed"] = True
            evidence["checks_failed"].append("wrong_act_status")
    except AdiletNetworkError:
        evidence["unverifiable"].append(f"act_info_unreachable:{doc_id}")
        return evidence
    except AdiletError:
        evidence["checks_failed"].append("nonexistent_act")
        return evidence

    if article or quote:
        key = (doc_id, article)
        try:
            if key not in text_cache:
                text_cache[key] = client.get_act_text(doc_id, article=article, max_chars=200_000)
            text_result = text_cache[key]
            if article:
                missing = any(
                    str(w).startswith("article_not_found") for w in text_result.get("warnings", [])
                )
                evidence["article_found"] = not missing
                if missing:
                    evidence["checks_failed"].append("nonexistent_article")
            if quote and evidence["article_found"] is not False:
                ratio = _quote_ratio(quote, text_result.get("text", ""))
                evidence["quote_match_ratio"] = round(ratio, 3)
                evidence["quote_verified"] = ratio >= QUOTE_MATCH_THRESHOLD
                if not evidence["quote_verified"]:
                    evidence["checks_failed"].append("misquoted_norm")
        except AdiletNetworkError:
            evidence["unverifiable"].append(f"act_text_unreachable:{doc_id}")
        except AdiletError as exc:
            evidence["unverifiable"].append(f"act_text_error:{exc}")

    if linked:
        try:
            if doc_id not in links_cache:
                links_cache[doc_id] = client.get_act_links(doc_id)
            links = links_cache[doc_id]
            referenced = {
                (entry.get("url") or "").rsplit("/", 1)[-1]
                for section in ("from_document", "to_document")
                for entry in links.get(section, [])
            }
            evidence["link_verified"] = linked in referenced
            if not evidence["link_verified"]:
                evidence["checks_failed"].append("unsupported_inter_act_link")
        except AdiletError:
            evidence["unverifiable"].append(f"act_links_unreachable:{doc_id}")

    return evidence


def _build_review_messages(question, answer_markdown, evidence) -> list[dict]:
    system = (
        "You are Hermes' adversarial legal answer reviewer for Kazakhstan law. "
        "Your job is to find defects, not to be agreeable. Check: citations "
        "supported by evidence; no inference presented as norm text; no invented "
        "links between acts; limitations disclosed. Findings are typed — the TYPE "
        "decides the verdict, not the severity. Return JSON only. No markdown."
    )
    user = json.dumps({
        "expected_schema": _REVIEW_SCHEMA,
        "question": question,
        "answer_markdown": answer_markdown,
        "deterministic_evidence": evidence,
    }, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def resolve_legal_review_model() -> tuple[str, str]:
    """(provider, model) for the review call — config-pinned, never hardcoded."""
    from hermes_cli.review_gate import resolve_reviewer_model

    resolved = resolve_reviewer_model(_REVIEW_TIER)
    return resolved["provider"], resolved["model"]


def _default_llm_call(messages, provider, model) -> str:
    # Mirrors hermes_cli.review_gate.run_code_review's invocation path.
    from agent.auxiliary_client import resolve_provider_client

    client, resolved_model = resolve_provider_client(
        provider,
        model,
        raw_codex=False,
        async_mode=False,
    )
    if client is None:
        raise RuntimeError(f"unable to resolve client for {provider} / {model}")
    response = client.chat.completions.create(
        model=resolved_model or model,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
    )
    choice = response.choices[0]
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not content:
        raise RuntimeError("review model returned empty content")
    return str(content)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", raw, flags=re.S)
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1] if start != -1 and end > start else raw


def run_legal_review(question, answer_markdown, answer_kind, citations, llm_call=None) -> dict:
    """Two-stage review of a drafted legal answer.

    Stage 1 always runs (deterministic). Stage 2 (LLM verdict on the pinned
    ``legal_review`` tier) runs for ``conclusions`` answers, or when stage 1
    found hard failures. Hard finding types force ``changes_requested``.
    A failed review call yields ``review_unavailable`` — fail open with
    disclosure, stage-1 evidence still attached.
    """
    evidence = verify_citations(citations or [])
    stage1_failures = sorted({code for e in evidence for code in e["checks_failed"]})

    result = {
        "verdict": "approved",
        "findings": [],
        "summary": "",
        "stage1_evidence": evidence,
        "report_path": None,
    }

    if answer_kind != "conclusions" and not stage1_failures:
        return _finalize(result, question)

    llm_call = llm_call or _default_llm_call
    try:
        provider, model = resolve_legal_review_model()
    except Exception:  # noqa: BLE001 — resolution failure must not kill the review
        provider, model = "openai-codex", "gpt-5.6-terra"
    try:
        raw = llm_call(_build_review_messages(question, answer_markdown, evidence), provider, model)
        parsed = json.loads(_extract_json(raw))
        result["findings"] = [f for f in parsed.get("findings", []) if isinstance(f, dict)]
        result["summary"] = str(parsed.get("summary", ""))
        result["verdict"] = str(parsed.get("verdict", "changes_requested"))
    except Exception as exc:  # noqa: BLE001 — review must fail open with disclosure
        result["verdict"] = "review_unavailable"
        result["summary"] = f"review model call failed: {exc}"

    finding_types = {str(f.get("type")) for f in result["findings"]}
    if (finding_types & HARD_FINDING_TYPES) or stage1_failures:
        for code in stage1_failures:
            if code not in finding_types:
                result["findings"].append({
                    "type": code,
                    "severity": "high",
                    "quote": "",
                    "explanation": "deterministic citation check failed",
                    "suggested_fix": "исправить или удалить утверждение",
                })
        if result["verdict"] != "review_unavailable":
            result["verdict"] = "changes_requested"

    return _finalize(result, question)


def _finalize(result: dict, question: str) -> dict:
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORT_DIR / f"legal_qa_{int(time.time() * 1000)}.json"
        path.write_text(
            json.dumps({"question": question, **result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["report_path"] = str(path)
    except OSError:
        pass
    return result
