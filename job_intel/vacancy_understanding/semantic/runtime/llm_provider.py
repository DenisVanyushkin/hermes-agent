"""LLM-backed semantic observation provider (Step 5A).

Second conformant implementation of the SemanticProvider Protocol.
Governed by semantic-provider-contract.md v1.0.0: emits ONLY observations,
verbatim evidence, basis = evidence class (never model self-confidence),
prompt is a versioned implementation detail, unknown = no observation.

No fallbacks of any kind: no model fallback, no provider fallback, no
phrase-provider fallback, no semantic repair. Invalid model output is an
explicit LLMProviderError, never a silent empty list.

Live paid calls are gated: record mode requires the explicit approval env
flag JOB_INTEL_LLM_LIVE_APPROVED=1 (spend gate, Roadmap SoT 9.5 / owner
approval). Replay mode reads recorded raw responses and performs zero
network calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from job_intel.vacancy_understanding.semantic.contract import (
    ExtractionClass,
    SemanticFactContract,
    load_semantic_contract,
)
from job_intel.vacancy_understanding.semantic.runtime.models import Observation
from job_intel.vacancy_understanding.semantic.runtime.pipeline import _values_for

LLM_PROVIDER_ID = "llm-observation"
LLM_PROMPT_VERSION = "llm-obs-1.0.0"
# Proposed Step 5A model (spend gate report); pinned exactly, no fallback.
DEFAULT_MODEL_ID = "openai/gpt-5-mini"
DEFAULT_TRANSPORT_PROVIDER = "openrouter"
DECODING_PARAMETERS: dict[str, Any] = {"temperature": 0}
RECORDING_FORMAT_VERSION = "1.0"
LIVE_APPROVAL_ENV = "JOB_INTEL_LLM_LIVE_APPROVED"
# OpenRouter must serve the pinned model itself — no upstream fallback routing.
NO_FALLBACK_EXTRA_BODY = {"provider": {"allow_fallbacks": False}}


def allowed_response_model(requested: str, actual: str) -> bool:
    """Model identity policy (Step 5A-4a).

    Accepts ONLY:
    - the exact requested slug (with or without the vendor prefix); or
    - a dated canonical snapshot of the SAME deployment:
      ``<slug>-YYYY-MM-DD`` (this is how OpenAI-family endpoints report the
      resolved snapshot of a stable alias).
    Anything else — другая модель, вариант семейства (gpt-5-nano,
    gpt-5-mini-high), другой vendor — отклоняется. Никаких startswith по
    семейству.
    """
    if not actual:
        return False
    base = requested.split("/", 1)[-1]
    if actual in (requested, base):
        return True
    return re.fullmatch(
        rf"(?:{re.escape(requested)}|{re.escape(base)})-\d{{4}}-\d{{2}}-\d{{2}}",
        actual) is not None

_BASIS_DEFINITIONS = {
    # Aligned 1:1 with the Semantic Contract confidence policy; basis is the
    # evidence class of the excerpt, NEVER the model's own certainty.
    "explicit": "the excerpt is a near-paraphrase assertion of the signal in the body",
    "direct": "the excerpt is direct responsibility/mandate language supporting the signal",
    "weak": "title-only or boilerplate-adjacent phrasing; a single weak cue",
}


class LLMProviderError(RuntimeError):
    """Explicit, visible provider failure. reason is a stable machine code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# ---------------------------------------------------------------------------
# Prompt (implementation detail; any change requires LLM_PROMPT_VERSION bump)
# ---------------------------------------------------------------------------

def _signal_leaf(fact_id: str) -> str:
    return fact_id[len("mandate."):] if fact_id.startswith("mandate.") else fact_id


def signal_vocabulary(contract: SemanticFactContract) -> dict[str, list[str]]:
    """Machine-derived allowed signals: fact leaf -> sorted enum values.

    Built from the contract fact inventory (semantic_only + hybrid only) and
    the existing Step 2 enum vocabularies used by runtime validation, so the
    prompt can never drift from what Stage 3 accepts.
    """
    vocab: dict[str, list[str]] = {}
    for fact in contract.facts:
        if fact.extraction_class not in (ExtractionClass.semantic_only, ExtractionClass.hybrid):
            continue
        values = _values_for(fact.id)
        if not values:
            continue  # non-enum facts (free text) are not signal targets
        vocab[_signal_leaf(fact.id)] = sorted(values)
    return vocab


def build_prompt(contract: SemanticFactContract) -> str:
    max_len = contract.observation_model.max_excerpt_len
    vocab_lines = "\n".join(
        f"- {leaf} = {' | '.join(values)}" for leaf, values in sorted(signal_vocabulary(contract).items())
    )
    basis_lines = "\n".join(f"- {k}: {v}" for k, v in _BASIS_DEFINITIONS.items())
    return f"""You extract semantic observations from a single job vacancy.

You will receive the vacancy TITLE and DESCRIPTION. Return ONLY a JSON object
of the form {{"observations": [...]}} matching the provided schema. No prose,
no markdown, no explanations outside the JSON.

An observation records ONE signal supported by ONE verbatim quote:
- observation_id: "obs-1", "obs-2", ... in order of appearance.
- excerpt: an EXACT verbatim substring of the TITLE or DESCRIPTION, at most
  {max_len} characters. Never paraphrase, never join fragments, never add
  ellipses. Copy the characters exactly as they appear.
- location: "title" if the excerpt comes from the TITLE, otherwise
  "description". The excerpt must be a substring of that exact source.
- signal_type: "<signal>=<value>" using ONLY the allowed vocabulary below.
- interpretation: 1-2 short sentences stating why this excerpt supports the
  signal, based on the excerpt alone. No hidden reasoning chains.
- maps_to: the canonical fact id list for the signal ("mandate.<signal>"
  unless the signal already starts with "company.", "requirements." or
  "organization.").
- basis: the evidence class of the excerpt (NOT your own confidence):
{basis_lines}

Allowed signals and values (anything else is forbidden):
{vocab_lines}

Hard rules:
1. Evidence only from the given text. You have NO knowledge of the company,
   brand, market or any other vacancy. Never use outside knowledge.
2. If the text contains no qualifying evidence for a signal, emit NOTHING for
   it. An empty observations list is a fully valid answer.
3. Never emit "=unknown" and never invent an observation to fill a gap.
4. Never assess whether the job suits any candidate; never produce
   recommendations, scores, probabilities or numeric confidence.
5. basis reflects how the evidence is phrased, never how sure you feel.
"""


def response_schema() -> dict[str, Any]:
    """Structured-output schema derived from the existing Observation model.

    ``$defs`` are hoisted to the document root: ``$ref: #/$defs/...`` inside
    the nested item schema resolves from the root, so leaving them under
    ``items`` produces a dangling reference the API rejects (400
    invalid_json_schema — hit on the first smoke attempt, 2026-07-19).
    """
    obs_schema = Observation.model_json_schema()
    defs = obs_schema.pop("$defs", None)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"observations": {"type": "array", "items": obs_schema}},
        "required": ["observations"],
        "additionalProperties": False,
    }
    if defs:
        schema["$defs"] = defs
    return schema


# ---------------------------------------------------------------------------
# Parser: technical normalization only, no repair, no policy
# ---------------------------------------------------------------------------

def parse_llm_response(raw_text: str) -> list[Observation]:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMProviderError("invalid_json", str(exc)) from exc
    if not isinstance(payload, dict) or "observations" not in payload:
        raise LLMProviderError("schema_invalid", "top-level object with 'observations' required")
    items = payload["observations"]
    if not isinstance(items, list):
        raise LLMProviderError("schema_invalid", "'observations' must be a list")
    out: list[Observation] = []
    for i, item in enumerate(items):
        if isinstance(item, dict) and str(item.get("signal_type", "")).endswith("=unknown"):
            # Contract 2.4: unknown is the ABSENCE of an observation. A model
            # that emits it violated the prompt; fail visibly, do not drop.
            raise LLMProviderError("schema_invalid", f"observations[{i}]: fact=unknown is forbidden")
        try:
            out.append(Observation.model_validate(item))
        except Exception as exc:  # pydantic ValidationError and friends
            raise LLMProviderError("schema_invalid", f"observations[{i}]: {exc}") from exc
    return out


# ---------------------------------------------------------------------------
# Record / replay store
# ---------------------------------------------------------------------------

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class RecordingStore:
    """One JSON file per call, keyed by input hash. No secrets are stored."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, input_hash: str) -> Path:
        return self.root / f"{input_hash}.json"

    def save(self, record: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.path_for(record["input_hash"])
        p.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return p

    def load(self, input_hash: str) -> dict[str, Any]:
        p = self.path_for(input_hash)
        if not p.exists():
            raise LLMProviderError("recording_missing", input_hash)
        try:
            record = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise LLMProviderError("recording_corrupt", f"{p.name}: {exc}") from exc
        if record.get("recording_format_version") != RECORDING_FORMAT_VERSION:
            raise LLMProviderError("recording_format_mismatch", str(record.get("recording_format_version")))
        raw = record.get("raw_response_text")
        if raw is None or _sha256(raw) != record.get("response_hash"):
            raise LLMProviderError("recording_corrupt", f"{p.name}: response hash mismatch")
        return record


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class LLMObservationProvider:
    """SemanticProvider implementation backed by one pinned LLM.

    mode="replay": reads recorded raw responses; zero network calls.
    mode="record": performs one live call per input via the injected
    transport (an OpenAI-compatible client) and records everything needed
    for offline replay. Building a live transport is gated separately in
    build_live_llm_provider().
    """

    provider_id = LLM_PROVIDER_ID
    prompt_version = LLM_PROMPT_VERSION

    def __init__(
        self,
        *,
        store: RecordingStore,
        mode: str = "replay",
        model_id: str = DEFAULT_MODEL_ID,
        transport: Any = None,
        contract: Optional[SemanticFactContract] = None,
    ) -> None:
        if mode not in ("replay", "record"):
            raise LLMProviderError("invalid_mode", mode)
        if mode == "record" and transport is None:
            raise LLMProviderError("transport_required", "record mode needs a transport client")
        if mode == "replay" and transport is not None:
            raise LLMProviderError("replay_must_be_offline", "no transport allowed in replay mode")
        self.mode = mode
        self.model_id = model_id
        self.store = store
        self._transport = transport
        self._contract = contract or load_semantic_contract()
        self._prompt = build_prompt(self._contract)
        self.last_call_metadata: dict[str, Any] = {}

    # -- identity -----------------------------------------------------------
    def input_hash(self, *, title: str, text: str, structured: dict) -> str:
        return _sha256(_canonical({
            "provider_id": self.provider_id,
            "prompt_version": self.prompt_version,
            "model_id": self.model_id,
            "decoding_parameters": DECODING_PARAMETERS,
            "title": title,
            "text": text,
            "structured": structured,
        }))

    # -- SemanticProvider Protocol -------------------------------------------
    def extract_semantic_observations(
        self, *, title: str, text: str, structured: dict
    ) -> list[Observation]:
        ih = self.input_hash(title=title, text=text, structured=structured)
        if self.mode == "replay":
            record = self.store.load(ih)
            if record.get("model_id") != self.model_id:
                raise LLMProviderError("model_version_mismatch", str(record.get("model_id")))
            if record.get("prompt_version") != self.prompt_version:
                raise LLMProviderError("prompt_version_mismatch", str(record.get("prompt_version")))
            if record.get("error"):
                raise LLMProviderError("recorded_call_failed", str(record["error"]))
            observations = parse_llm_response(record["raw_response_text"])
            self.last_call_metadata = {
                "mode": "replay", "input_hash": ih,
                "usage": record.get("usage"), "latency_ms": record.get("latency_ms"),
                "model_id": record.get("model_id"), "retry_count": record.get("retry_count", 0),
            }
            return observations
        return self._record_call(ih, title=title, text=text, structured=structured)

    # -- live call (record mode only) -----------------------------------------
    def _record_call(self, ih: str, *, title: str, text: str, structured: dict) -> list[Observation]:
        user_content = f"TITLE:\n{title}\n\nDESCRIPTION:\n{text}"
        started = time.monotonic()
        error: Optional[str] = None
        raw_text: Optional[str] = None
        usage: Optional[dict] = None
        response_model: Optional[str] = None
        try:
            response = self._transport.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": self._prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "semantic_observations",
                                    "schema": response_schema(), "strict": True},
                },
                extra_body=NO_FALLBACK_EXTRA_BODY,
                **DECODING_PARAMETERS,
            )
            choice = response.choices[0]
            raw_text = getattr(getattr(choice, "message", None), "content", None)
            if not raw_text:
                error = "empty_response"
            u = getattr(response, "usage", None)
            if u is not None:
                usage = u if isinstance(u, dict) else {
                    "prompt_tokens": getattr(u, "prompt_tokens", None),
                    "completion_tokens": getattr(u, "completion_tokens", None),
                    "total_tokens": getattr(u, "total_tokens", None),
                }
            response_model = getattr(response, "model", None)
            # Model identity verification BEFORE anything reaches the parser.
            if error is None:
                if not response_model:
                    error = "model_identity_unverifiable: response.model missing"
                elif not allowed_response_model(self.model_id, response_model):
                    error = (f"model_version_mismatch: requested {self.model_id}, "
                             f"response served by {response_model}")
        except Exception as exc:  # transport failure: record + surface, no retry here
            error = f"transport_error: {exc}"
        latency_ms = int((time.monotonic() - started) * 1000)
        record = {
            "recording_format_version": RECORDING_FORMAT_VERSION,
            "input_hash": ih,
            "requested_model": self.model_id,
            "input": {"title": title, "text": text, "structured": structured},
            "provider_id": self.provider_id,
            "prompt_version": self.prompt_version,
            "model_id": self.model_id,
            "response_model": response_model,
            "decoding_parameters": DECODING_PARAMETERS,
            "request_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "raw_response_text": raw_text if raw_text is not None else "",
            "response_hash": _sha256(raw_text if raw_text is not None else ""),
            "usage": usage,
            "latency_ms": latency_ms,
            "retry_count": 0,
            "error": error,
        }
        self.store.save(record)
        self.last_call_metadata = {
            "mode": "record", "input_hash": ih, "usage": usage,
            "latency_ms": latency_ms, "model_id": self.model_id, "retry_count": 0,
            "error": error,
        }
        if error:
            reason = error.split(":", 1)[0]
            raise LLMProviderError(reason, error)
        return parse_llm_response(raw_text)


def build_live_llm_provider(
    *, store_dir: Path | str, model_id: str = DEFAULT_MODEL_ID
) -> LLMObservationProvider:
    """Spend-gated factory for record mode. Refuses to build without the
    explicit owner-approval flag; replay mode never needs this factory."""
    if os.environ.get(LIVE_APPROVAL_ENV) != "1":
        raise LLMProviderError(
            "live_calls_not_approved",
            f"set {LIVE_APPROVAL_ENV}=1 only after the Step 5A spend gate is approved")
    from agent.auxiliary_client import resolve_provider_client

    client, resolved_model = resolve_provider_client(DEFAULT_TRANSPORT_PROVIDER, model=model_id)
    if client is None:
        raise LLMProviderError("transport_unavailable", DEFAULT_TRANSPORT_PROVIDER)
    if resolved_model and resolved_model != model_id:
        raise LLMProviderError("model_version_mismatch",
                               f"requested {model_id}, transport resolved {resolved_model}")
    # SDK default is 2 SILENT retries; until attempt accounting exists,
    # invisible retries are forbidden — recordings must honestly say
    # retry_count=0 (Step 5A-4a).
    if not hasattr(client, "with_options"):
        raise LLMProviderError(
            "transport_unsupported",
            "client cannot disable SDK retries (with_options missing); "
            "STEP_5A_PROVIDER_TRANSPORT_BLOCKED")
    client = client.with_options(max_retries=0)
    return LLMObservationProvider(
        store=RecordingStore(store_dir), mode="record",
        model_id=model_id, transport=client)
