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

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import math
import os
import re
import stat
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
LLM_PROMPT_VERSION = "llm-obs-1.0.0"  # default/frozen baseline
# sha256 of build_prompt(load_semantic_contract()) — the 1.0.0 prompt is
# frozen as the 5B benchmark baseline; a guard test pins this so the text can
# never drift and orphan every recording keyed on it.
FROZEN_PROMPT_V1_0_0_SHA256 = (
    "051c18e40d15d65c15309d217831044267bf01abb77ac5019cbb6ec29e1d29d3")
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


@dataclass(frozen=True)
class GovernedPricingSchedule:
    """Pinned rates and token ceilings used by governed structured calls."""

    version: str
    model_id: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    max_input_tokens: int
    max_output_tokens: int

    def __post_init__(self) -> None:
        if not self.version or self.model_id != DEFAULT_MODEL_ID:
            raise LLMProviderError("pricing_identity_invalid")
        for value in (self.input_usd_per_mtok, self.output_usd_per_mtok):
            if not value.is_finite() or value < 0:
                raise LLMProviderError("pricing_rate_invalid")
        if self.max_input_tokens <= 0 or self.max_output_tokens <= 0:
            raise LLMProviderError("pricing_token_bound_invalid")

    def as_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_id": self.model_id,
            "input_usd_per_mtok": str(self.input_usd_per_mtok),
            "output_usd_per_mtok": str(self.output_usd_per_mtok),
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }

    @property
    def identity_sha256(self) -> str:
        return _sha256(_canonical(self.as_record()))

    @property
    def reservation_cost_usd(self) -> Decimal:
        return self.cost(
            prompt_tokens=self.max_input_tokens,
            completion_tokens=self.max_output_tokens,
        )

    def validate_usage(self, usage: object) -> dict[str, int]:
        values = usage if isinstance(usage, dict) else {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        if any(
            isinstance(values.get(name), bool)
            or not isinstance(values.get(name), int)
            or values[name] < 0
            for name in names
        ):
            raise LLMProviderError("usage_invalid", "token fields")
        normalized = {name: int(values[name]) for name in names}
        if normalized["total_tokens"] != (
            normalized["prompt_tokens"] + normalized["completion_tokens"]
        ):
            raise LLMProviderError("usage_invalid", "total_tokens")
        if normalized["prompt_tokens"] > self.max_input_tokens:
            raise LLMProviderError("usage_invalid", "prompt token bound")
        if normalized["completion_tokens"] > self.max_output_tokens:
            raise LLMProviderError("usage_invalid", "completion token bound")
        return normalized

    def cost(self, *, prompt_tokens: int, completion_tokens: int) -> Decimal:
        value = (
            Decimal(prompt_tokens) * self.input_usd_per_mtok
            + Decimal(completion_tokens) * self.output_usd_per_mtok
        ) / Decimal(1_000_000)
        return value.quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class GovernedStructuredRequest:
    input_hash: str
    system_prompt: str
    user_payload: dict[str, Any]
    schema_name: str
    response_schema: dict[str, Any]
    governance_identity: dict[str, Any]
    forbidden_markers: tuple[str, ...] = ()
    response_validator: Any = None


@dataclass(frozen=True)
class GovernedStructuredResult:
    raw_response_text: str
    usage: dict[str, int]
    cost_usd: Decimal
    latency_ms: int
    response_model: str
    record: dict[str, Any]


@dataclass(frozen=True)
class GovernedStructuredTerminalUnknown:
    """Sealed post-dispatch result whose provider cost cannot be trusted."""

    raw_response_text: str
    latency_ms: int
    failure_code: str
    failure_diagnostic: str
    conservative_cost_usd: Decimal
    record: dict[str, Any]


_CAPABILITY_ISSUER = object()


class StructuredCallCapability:
    """Opaque runtime permit issued only after an external runner authorizes."""

    def __init__(
        self,
        *,
        issuer: object,
        run_identity_sha256: str,
        pricing: GovernedPricingSchedule,
        exact_call_cap: int,
        exact_spend_cap_usd: Decimal,
        metadata_seal_key: bytes,
        reserve: Any,
        mark_dispatching: Any,
        reconcile: Any,
        capture_record: Any = None,
        bind_record_identity: Any = None,
    ) -> None:
        if issuer is not _CAPABILITY_ISSUER:
            raise LLMProviderError("structured_capability_invalid")
        self.run_identity_sha256 = run_identity_sha256
        self.pricing = pricing
        self.exact_call_cap = exact_call_cap
        self.exact_spend_cap_usd = exact_spend_cap_usd
        if not isinstance(metadata_seal_key, bytes) or len(metadata_seal_key) < 16:
            raise LLMProviderError("metadata_seal_key_invalid")
        self._metadata_seal_key = metadata_seal_key
        self._reserve = reserve
        self._mark_dispatching = mark_dispatching
        self._reconcile = reconcile
        self._capture_record = capture_record
        self._bind_record_identity = bind_record_identity
        self._record_receipts: dict[str, object] = {}

    def reserve(self, input_hash: str) -> str:
        return str(self._reserve(input_hash, self.pricing.reservation_cost_usd))

    def mark_dispatching(self, reservation_id: str) -> None:
        self._mark_dispatching(reservation_id)

    def reconcile(
        self, reservation_id: str, actual_cost: Decimal, outcome: str
    ) -> None:
        self._reconcile(reservation_id, actual_cost, outcome)

    def bind_record_identity(
        self, dispatch_input_hash: str, provider_input_hash: str
    ) -> None:
        if self._bind_record_identity is not None:
            self._bind_record_identity(dispatch_input_hash, provider_input_hash)

    def receipt_for_input_hash(self, input_hash: str) -> object | None:
        return self._record_receipts.get(input_hash)

    def seal_record(self, record: dict[str, Any]) -> dict[str, Any]:
        unsigned = {
            key: value
            for key, value in record.items()
            if key not in {"metadata_sha256", "metadata_hmac_sha256"}
        }
        metadata_sha256 = _sha256(_canonical(unsigned))
        record["metadata_sha256"] = metadata_sha256
        record["metadata_hmac_sha256"] = hmac.new(
            self._metadata_seal_key,
            metadata_sha256.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if self._capture_record is not None:
            receipt = self._capture_record(dict(record))
            if receipt is not None:
                input_hash = record.get("input_hash")
                if not isinstance(input_hash, str) or not input_hash:
                    raise LLMProviderError("provider_record_input_hash_missing")
                self._record_receipts[input_hash] = receipt
        return record

    def verify_record(self, record: dict[str, Any]) -> None:
        expected = dict(record)
        supplied_hmac = expected.pop("metadata_hmac_sha256", None)
        supplied_sha = expected.pop("metadata_sha256", None)
        calculated_sha = _sha256(_canonical(expected))
        calculated_hmac = hmac.new(
            self._metadata_seal_key,
            calculated_sha.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if supplied_sha != calculated_sha or not isinstance(
            supplied_hmac, str
        ) or not hmac.compare_digest(supplied_hmac, calculated_hmac):
            raise LLMProviderError("provider_metadata_mismatch", "metadata_seal")


def _issue_structured_call_capability(
    *,
    run_identity_sha256: str,
    pricing: GovernedPricingSchedule,
    exact_call_cap: int,
    exact_spend_cap_usd: Decimal,
    metadata_seal_key: bytes,
    reserve: Any,
    mark_dispatching: Any,
    reconcile: Any,
    capture_record: Any = None,
    bind_record_identity: Any = None,
) -> StructuredCallCapability:
    """Bridge an already-authorized transactional runner into the runtime."""
    if not re.fullmatch(r"[0-9a-f]{64}", run_identity_sha256):
        raise LLMProviderError("structured_capability_identity_invalid")
    if exact_call_cap <= 0 or not exact_spend_cap_usd.is_finite():
        raise LLMProviderError("structured_capability_cap_invalid")
    return StructuredCallCapability(
        issuer=_CAPABILITY_ISSUER,
        run_identity_sha256=run_identity_sha256,
        pricing=pricing,
        exact_call_cap=exact_call_cap,
        exact_spend_cap_usd=exact_spend_cap_usd,
        metadata_seal_key=metadata_seal_key,
        reserve=reserve,
        mark_dispatching=mark_dispatching,
        reconcile=reconcile,
        capture_record=capture_record,
        bind_record_identity=bind_record_identity,
    )


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


# 1.1.0 — additive change scoped by the 5B-5 owner review: the provider was
# reliable on company facts but over-reached on mandate facts, inferring the
# candidate's remit (scope_breadth, revenue_proximity, strategy_ownership,
# ...) from company-level phrasing (scale, valuation, brand, ecosystem, HR
# boilerplate). This block forbids exactly that, and nothing else.
_MANDATE_GATING_RULE_V1_1_0 = """
6. mandate.* signals describe what THIS ROLE owns or does — the candidate's
   remit — and require evidence that describes the role's responsibilities.
   NEVER infer a mandate signal from evidence that only describes the
   COMPANY: its scale ("over 200,000 businesses", "26 offices"), valuation
   or funding ("valued at US$8bn", "backed by ..."), brand or product
   portfolio, market position, ecosystem, or hiring/culture boilerplate
   ("we hire builders with founder-like energy"). Such quotes may support a
   company.* signal but MUST NOT be used as maps_to a mandate.* fact. When a
   role's mandate is not stated in responsibility language, emit nothing for
   it — a company fact is not a mandate."""


def build_prompt_v1_1_0(contract: SemanticFactContract) -> str:
    return build_prompt(contract) + _MANDATE_GATING_RULE_V1_1_0


# fact-leaf -> builder. 1.0.0 stays the default and is frozen.
PROMPT_BUILDERS = {
    "llm-obs-1.0.0": build_prompt,
    "llm-obs-1.1.0": build_prompt_v1_1_0,
}


def build_prompt_for_version(version: str, contract: SemanticFactContract) -> str:
    builder = PROMPT_BUILDERS.get(version)
    if builder is None:
        raise LLMProviderError("unknown_prompt_version", version)
    return builder(contract)


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
        self._root_descriptor: Optional[int] = None

    def _directory_descriptor(self) -> int:
        if self._root_descriptor is not None:
            return self._root_descriptor
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            parent_descriptor = os.open(self.root.parent, flags)
        except OSError as exc:
            raise LLMProviderError("recording_root_unsafe", self.root.name) from exc
        try:
            try:
                os.mkdir(self.root.name, mode=0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            try:
                descriptor = os.open(
                    self.root.name, flags, dir_fd=parent_descriptor
                )
            except OSError as exc:
                raise LLMProviderError(
                    "recording_root_unsafe", self.root.name
                ) from exc
        finally:
            os.close(parent_descriptor)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise LLMProviderError("recording_root_unsafe", self.root.name)
        os.fchmod(descriptor, 0o700)
        self._root_descriptor = descriptor
        return descriptor

    def close(self) -> None:
        if self._root_descriptor is not None:
            os.close(self._root_descriptor)
            self._root_descriptor = None

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def path_for(self, input_hash: str) -> Path:
        return self.root / f"{input_hash}.json"

    def exists(self, input_hash: str) -> bool:
        directory_descriptor = self._directory_descriptor()
        try:
            file_stat = os.stat(
                f"{input_hash}.json",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return stat.S_ISREG(file_stat.st_mode)

    def save(self, record: dict[str, Any]) -> Path:
        directory_descriptor = self._directory_descriptor()
        p = self.path_for(record["input_hash"])
        temporary = f".{p.name}.{os.getpid()}.tmp"
        payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o600)
            os.replace(
                temporary,
                p.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        return p

    def save_exclusive(self, record: dict[str, Any]) -> Path:
        directory_descriptor = self._directory_descriptor()
        path = self.path_for(record["input_hash"])
        temporary = f".{path.name}.{os.getpid()}.tmp"
        payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_descriptor,
            )
        except FileExistsError as exc:
            raise LLMProviderError("recording_write_in_progress", path.name) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o600)
            try:
                os.link(
                    temporary,
                    path.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise LLMProviderError("recording_exists", path.name) from exc
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        return path

    def load(self, input_hash: str) -> dict[str, Any]:
        p = self.path_for(input_hash)
        directory_descriptor = self._directory_descriptor()
        try:
            descriptor = os.open(
                p.name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError as exc:
            raise LLMProviderError("recording_missing", input_hash) from exc
        except OSError as exc:
            raise LLMProviderError("recording_unsafe", p.name) from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise LLMProviderError("recording_not_regular", p.name)
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                record = json.load(stream)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("recording_corrupt", f"{p.name}: {exc}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if record.get("recording_format_version") != RECORDING_FORMAT_VERSION:
            raise LLMProviderError("recording_format_mismatch", str(record.get("recording_format_version")))
        raw = record.get("raw_response_text")
        if raw is None or _sha256(raw) != record.get("response_hash"):
            raise LLMProviderError("recording_corrupt", f"{p.name}: response hash mismatch")
        metadata_sha256 = record.get("metadata_sha256")
        if metadata_sha256 is not None:
            unsigned = {
                key: value
                for key, value in record.items()
                if key not in {"metadata_sha256", "metadata_hmac_sha256"}
            }
            if _sha256(_canonical(unsigned)) != metadata_sha256:
                raise LLMProviderError("recording_corrupt", f"{p.name}: metadata hash mismatch")
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

    def __init__(
        self,
        *,
        store: RecordingStore,
        mode: str = "replay",
        model_id: str = DEFAULT_MODEL_ID,
        transport: Any = None,
        contract: Optional[SemanticFactContract] = None,
        prompt_version: str = LLM_PROMPT_VERSION,
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
        # prompt_version is instance state: it participates in input_hash and
        # is stored on every recording, so a 1.1.0 run is a distinct benchmark
        # identity that never collides with or overwrites 1.0.0 recordings.
        self.prompt_version = prompt_version
        self._prompt = build_prompt_for_version(prompt_version, self._contract)
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

    @property
    def semantic_prompt_sha256(self) -> str:
        """Public immutable identity of this provider's Semantic prompt."""
        return _sha256(self._prompt)

    def governed_structured_call(
        self,
        *,
        request: GovernedStructuredRequest,
        capability: StructuredCallCapability,
    ) -> GovernedStructuredResult | GovernedStructuredTerminalUnknown:
        """Execute and atomically record one capability-governed JSON call.

        This is the sole public structured-call transport boundary.  The
        caller supplies only a pinned prompt/schema/redacted input; this
        runtime owns transport entry, model/usage checks, cost accounting,
        sanitization, sealing, and exclusive recording creation.
        """
        if self.mode != "record":
            raise LLMProviderError("structured_call_requires_record_mode")
        if not isinstance(capability, StructuredCallCapability):
            raise LLMProviderError("structured_capability_required")
        if request.governance_identity.get("run_identity_sha256") != (
            capability.run_identity_sha256
        ):
            raise LLMProviderError("structured_capability_identity_mismatch")
        if self.store.exists(request.input_hash):
            raise LLMProviderError("recording_exists", request.input_hash)
        serialized_input = _canonical(request.user_payload)
        folded_input = serialized_input.casefold()
        if any(marker.casefold() in folded_input for marker in request.forbidden_markers):
            raise LLMProviderError("request_forbidden_marker")

        reservation_cost = capability.pricing.reservation_cost_usd
        reservation_id = capability.reserve(request.input_hash)
        # Persist the conservative charge-unknown state before entering the
        # provider. A crash after this point can never be retried implicitly.
        capability.mark_dispatching(reservation_id)
        started = time.monotonic()
        raw_text = ""
        response_model: Optional[str] = None
        usage: Optional[dict[str, int]] = None
        measured_cost: Optional[Decimal] = None
        failure_code: Optional[str] = None
        failure_diagnostic: Optional[str] = None
        try:
            response = self._transport.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": serialized_input},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "schema": request.response_schema,
                        "strict": True,
                    },
                },
                extra_body=NO_FALLBACK_EXTRA_BODY,
                max_tokens=capability.pricing.max_output_tokens,
                **DECODING_PARAMETERS,
            )
            response_model = getattr(response, "model", None)
            raw_usage = getattr(response, "usage", None)
            try:
                usage = capability.pricing.validate_usage(raw_usage)
                measured_cost = capability.pricing.cost(
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                )
                if measured_cost > reservation_cost:
                    failure_code = "cost_bound_exceeded"
                    failure_diagnostic = "reservation"
            except LLMProviderError as exc:
                failure_code = exc.reason
                failure_diagnostic = "usage_validation"
            parsed_payload: Any = None
            try:
                choice = response.choices[0]
                message = getattr(choice, "message", None)
                refusal = getattr(message, "refusal", None)
                content = getattr(message, "content", None)
            except Exception:
                refusal = None
                content = None
                if failure_code is None:
                    failure_code = "schema_invalid"
                    failure_diagnostic = "response_shape"
            if refusal:
                failure_code = "refusal"
                failure_diagnostic = "provider_refusal"
            elif failure_code is None and (
                not isinstance(content, str) or not content
            ):
                failure_code = "schema_invalid"
                failure_diagnostic = "empty_response"
            elif failure_code is None and (
                not response_model
                or not allowed_response_model(self.model_id, response_model)
            ):
                failure_code = "provider_metadata_mismatch"
                failure_diagnostic = "served_model"
            elif failure_code is None:
                raw_text = content
                if any(
                    marker.casefold() in raw_text.casefold()
                    for marker in request.forbidden_markers
                ):
                    failure_code = "forbidden_response_marker"
                    failure_diagnostic = "redaction_guard"
                else:
                    try:
                        parsed_payload = json.loads(raw_text)
                    except (json.JSONDecodeError, TypeError):
                        failure_code = "schema_invalid"
                        failure_diagnostic = "invalid_json"
                if failure_code is None and request.response_validator is not None:
                    try:
                        validation_code = request.response_validator(parsed_payload)
                    except Exception:
                        validation_code = "result_validation_failed"
                    if validation_code is not None:
                        normalized_code = str(validation_code)
                        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", normalized_code):
                            normalized_code = "result_validation_failed"
                        failure_code = normalized_code
                        failure_diagnostic = "response_validator"
        except Exception as exc:
            failure_code = "timeout" if isinstance(exc, TimeoutError) else "transport_error"
            failure_diagnostic = type(exc).__name__[:80]

        latency_ms = int((time.monotonic() - started) * 1000)
        trustworthy_cost = measured_cost is not None and measured_cost <= reservation_cost
        if trustworthy_cost:
            terminal = "success" if failure_code is None else "terminal_failure"
            conservative_cost = measured_cost
        else:
            terminal = "terminal_unknown"
            usage = None
            measured_cost = None
            conservative_cost = reservation_cost
        persisted_raw = raw_text if terminal == "success" else ""
        record = {
            "recording_format_version": RECORDING_FORMAT_VERSION,
            "input_hash": request.input_hash,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "requested_model": self.model_id,
            "response_model": response_model,
            "semantic_prompt_version": self.prompt_version,
            "semantic_prompt_sha256": self.semantic_prompt_sha256,
            "structured_prompt_sha256": _sha256(request.system_prompt),
            "response_schema_sha256": _sha256(_canonical(request.response_schema)),
            "governance_identity": request.governance_identity,
            "input": request.user_payload,
            "input_payload_sha256": _sha256(serialized_input),
            "raw_response_text": persisted_raw,
            "response_hash": _sha256(persisted_raw),
            "usage": usage,
            "pricing": capability.pricing.as_record(),
            "pricing_sha256": capability.pricing.identity_sha256,
            "cost_usd": str(conservative_cost),
            "measured_cost_usd": (
                None if measured_cost is None else str(measured_cost)
            ),
            "conservative_cost_usd": str(conservative_cost),
            "post_dispatch_outcome_v3": terminal,
            "latency_ms": latency_ms,
            "retry_count": 0,
            "decoding_parameters": dict(DECODING_PARAMETERS),
            "max_output_tokens": capability.pricing.max_output_tokens,
            "status": "success" if terminal == "success" else "failure",
            "failure_code": failure_code,
            "failure_diagnostic": failure_diagnostic,
        }
        capability.seal_record(record)
        self.store.save_exclusive(record)
        capability.reconcile(
            reservation_id,
            conservative_cost,
            terminal,
        )
        self.last_call_metadata = {
            "mode": "record",
            "input_hash": request.input_hash,
            "usage": usage,
            "latency_ms": latency_ms,
            "model_id": self.model_id,
            "retry_count": 0,
            "cost_usd": str(conservative_cost),
            "measured_cost_usd": (
                None if measured_cost is None else str(measured_cost)
            ),
            "conservative_cost_usd": str(conservative_cost),
            "post_dispatch_outcome_v3": terminal,
            "sealed_provider_record_sha256": record["metadata_sha256"],
            "failure_code": failure_code,
        }
        if terminal == "terminal_unknown":
            return GovernedStructuredTerminalUnknown(
                raw_response_text="",
                latency_ms=latency_ms,
                failure_code=failure_code or "post_dispatch_unknown",
                failure_diagnostic=failure_diagnostic or "cost_evidence_missing",
                conservative_cost_usd=conservative_cost,
                record=record,
            )
        if failure_code:
            raise LLMProviderError(failure_code, failure_diagnostic or "")
        return GovernedStructuredResult(
            raw_response_text=persisted_raw,
            usage=usage or {},
            cost_usd=(
                measured_cost if measured_cost is not None else Decimal("0.000000")
            ),
            latency_ms=latency_ms,
            response_model=response_model or "",
            record=record,
        )

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
        # Record mode is idempotent per input (Slice 5B-4 finding): datasets
        # legitimately contain distinct cases with identical (title, text) —
        # re-calling live would both pay twice for the same input and
        # OVERWRITE the recording with a non-byte-identical response
        # (temperature=0 does not guarantee live-repeat equality), orphaning
        # every earlier case derived from the first response. A successful
        # recording for this exact input/model/prompt is therefore reused;
        # only failed recordings are retried live.
        if self.store.exists(ih):
            record = self.store.load(ih)
            if (not record.get("error")
                    and record.get("model_id") == self.model_id
                    and record.get("prompt_version") == self.prompt_version):
                observations = parse_llm_response(record["raw_response_text"])
                self.last_call_metadata = {
                    "mode": "record_cached", "input_hash": ih,
                    "usage": record.get("usage"), "latency_ms": record.get("latency_ms"),
                    "model_id": record.get("model_id"),
                    "retry_count": record.get("retry_count", 0),
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
    *, store_dir: Path | str, model_id: str = DEFAULT_MODEL_ID,
    prompt_version: str = LLM_PROMPT_VERSION,
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
        model_id=model_id, transport=client, prompt_version=prompt_version)
