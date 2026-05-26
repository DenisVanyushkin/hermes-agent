from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

MANIFEST_SCHEMA_VERSION = "1.0.0"


class RuntimeManifestError(ValueError):
    """Raised when a runtime manifest violates module-boundary rules."""


class StateDomain(StrEnum):
    """Canonical internal state domains owned by exactly one module."""

    RAW_MARKET_EVENTS = "raw_market_events"
    NORMALIZED_MARKET_STATE = "normalized_market_state"
    STRATEGY_PROPOSALS = "strategy_proposals"
    RISK_STATE = "risk_state"
    ACCOUNT_STATE = "account_state"
    EXECUTION_INTENTS = "execution_intents"
    ORDER_LIFECYCLE = "order_lifecycle"
    IMMUTABLE_JOURNAL = "immutable_journal"
    METRICS_SNAPSHOT = "metrics_snapshot"
    CONTROL_COMMANDS = "control_commands"
    STRATEGY_REGISTRY = "strategy_registry_state"


@dataclass(frozen=True, slots=True)
class ModuleContract:
    """Versioned contract for one runtime module."""

    name: str
    contract_version: str
    owns_state: tuple[StateDomain, ...]
    reads_state: tuple[StateDomain, ...] = field(default_factory=tuple)
    emits_state: tuple[StateDomain, ...] = field(default_factory=tuple)
    execution_mode: str = "sync"
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "contract_version": self.contract_version,
            "owns_state": [domain.value for domain in self.owns_state],
            "reads_state": [domain.value for domain in self.reads_state],
            "emits_state": [domain.value for domain in self.emits_state],
            "execution_mode": self.execution_mode,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ModuleContract":
        try:
            return cls(
                name=str(payload["name"]),
                contract_version=str(payload["contract_version"]),
                owns_state=tuple(StateDomain(item) for item in payload.get("owns_state", [])),
                reads_state=tuple(StateDomain(item) for item in payload.get("reads_state", [])),
                emits_state=tuple(StateDomain(item) for item in payload.get("emits_state", [])),
                execution_mode=str(payload.get("execution_mode", "sync")),
                notes=str(payload.get("notes", "")),
            )
        except KeyError as exc:
            raise RuntimeManifestError(f"Missing module contract field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise RuntimeManifestError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    """Top-level versioned runtime manifest for the trading autopilot."""

    schema_version: str
    modules: tuple[ModuleContract, ...]

    def validate(self) -> None:
        if not self.schema_version:
            raise RuntimeManifestError("schema_version must be set")

        names = [module.name for module in self.modules]
        if len(set(names)) != len(names):
            raise RuntimeManifestError("Module names must be unique")

        contract_versions = [module.contract_version for module in self.modules]
        if any(not version for version in contract_versions):
            raise RuntimeManifestError("Every module must have a contract_version")

        ownership_map: dict[StateDomain, str] = {}
        for module in self.modules:
            if module.execution_mode not in {"sync", "async", "hybrid"}:
                raise RuntimeManifestError(
                    f"Module {module.name} has invalid execution_mode: {module.execution_mode}"
                )
            if not module.owns_state:
                raise RuntimeManifestError(f"Module {module.name} must own at least one state domain")
            for domain in module.owns_state:
                previous = ownership_map.get(domain)
                if previous is not None:
                    raise RuntimeManifestError(
                        f"State domain {domain.value} is owned by both {previous} and {module.name}"
                    )
                ownership_map[domain] = module.name

        expected_domains = {domain for module in self.modules for domain in module.owns_state}
        if len(ownership_map) != len(expected_domains):
            raise RuntimeManifestError("Ownership map is inconsistent")

        for module in self.modules:
            owned = set(module.owns_state)
            reads = set(module.reads_state)
            emits = set(module.emits_state)
            if owned & reads:
                overlap = ", ".join(sorted(domain.value for domain in owned & reads))
                raise RuntimeManifestError(f"Module {module.name} reads state it owns: {overlap}")
            if not emits <= owned:
                overlap = ", ".join(sorted(domain.value for domain in emits - owned))
                raise RuntimeManifestError(
                    f"Module {module.name} emits unowned state: {overlap}"
                )

    def ownership_map(self) -> dict[str, str]:
        self.validate()
        return {
            domain.value: module.name
            for module in self.modules
            for domain in module.owns_state
        }

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "modules": [module.to_dict() for module in self.modules],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RuntimeManifest":
        try:
            modules = tuple(ModuleContract.from_dict(item) for item in payload["modules"])
            manifest = cls(schema_version=str(payload["schema_version"]), modules=modules)
            manifest.validate()
            return manifest
        except KeyError as exc:
            raise RuntimeManifestError(f"Missing manifest field: {exc.args[0]}") from exc

    def summary_lines(self) -> list[str]:
        self.validate()
        lines = [f"Runtime manifest schema={self.schema_version}"]
        for module in self.modules:
            owned = ", ".join(domain.value for domain in module.owns_state)
            lines.append(f"- {module.name} [{module.contract_version}] ({module.execution_mode}) owns: {owned}")
        return lines


def _module(
    name: str,
    owns_state: Iterable[StateDomain],
    reads_state: Iterable[StateDomain] = (),
    emits_state: Iterable[StateDomain] = (),
    execution_mode: str = "sync",
    notes: str = "",
) -> ModuleContract:
    return ModuleContract(
        name=name,
        contract_version="v1",
        owns_state=tuple(owns_state),
        reads_state=tuple(reads_state),
        emits_state=tuple(emits_state),
        execution_mode=execution_mode,
        notes=notes,
    )


DEFAULT_MANIFEST = RuntimeManifest(
    schema_version=MANIFEST_SCHEMA_VERSION,
    modules=(
        _module(
            "market_ingest",
            owns_state=(StateDomain.RAW_MARKET_EVENTS,),
            emits_state=(StateDomain.RAW_MARKET_EVENTS,),
            execution_mode="async",
            notes="Consumes exchange and tape inputs; never mutates downstream state.",
        ),
        _module(
            "market_normalizer",
            owns_state=(StateDomain.NORMALIZED_MARKET_STATE,),
            reads_state=(StateDomain.RAW_MARKET_EVENTS,),
            emits_state=(StateDomain.NORMALIZED_MARKET_STATE,),
            execution_mode="sync",
            notes="Deterministic market-state canonicalizer.",
        ),
        _module(
            "strategy_layer",
            owns_state=(StateDomain.STRATEGY_PROPOSALS,),
            reads_state=(
                StateDomain.NORMALIZED_MARKET_STATE,
                StateDomain.ACCOUNT_STATE,
            ),
            emits_state=(StateDomain.STRATEGY_PROPOSALS,),
            execution_mode="sync",
            notes="Strategy/LLM proposal layer; no direct execution authority.",
        ),
        _module(
            "risk_engine",
            owns_state=(StateDomain.RISK_STATE,),
            reads_state=(
                StateDomain.NORMALIZED_MARKET_STATE,
                StateDomain.STRATEGY_PROPOSALS,
                StateDomain.ACCOUNT_STATE,
            ),
            emits_state=(StateDomain.RISK_STATE,),
            execution_mode="sync",
            notes="Deterministic veto authority over all trade intents.",
        ),
        _module(
            "portfolio_state",
            owns_state=(StateDomain.ACCOUNT_STATE,),
            reads_state=(StateDomain.ORDER_LIFECYCLE,),
            emits_state=(StateDomain.ACCOUNT_STATE,),
            execution_mode="async",
            notes="Exchange-truth reconciliation and canonical account snapshot owner.",
        ),
        _module(
            "execution_engine",
            owns_state=(StateDomain.EXECUTION_INTENTS,),
            reads_state=(
                StateDomain.RISK_STATE,
                StateDomain.STRATEGY_PROPOSALS,
                StateDomain.ACCOUNT_STATE,
            ),
            emits_state=(StateDomain.EXECUTION_INTENTS,),
            execution_mode="sync",
            notes="Order submission boundary; no strategy logic.",
        ),
        _module(
            "order_manager",
            owns_state=(StateDomain.ORDER_LIFECYCLE,),
            reads_state=(StateDomain.EXECUTION_INTENTS,),
            emits_state=(StateDomain.ORDER_LIFECYCLE,),
            execution_mode="async",
            notes="Idempotent order lifecycle and exchange acknowledgement state machine.",
        ),
        _module(
            "journal_store",
            owns_state=(StateDomain.IMMUTABLE_JOURNAL,),
            reads_state=(
                StateDomain.RAW_MARKET_EVENTS,
                StateDomain.NORMALIZED_MARKET_STATE,
                StateDomain.STRATEGY_PROPOSALS,
                StateDomain.RISK_STATE,
                StateDomain.ACCOUNT_STATE,
                StateDomain.EXECUTION_INTENTS,
                StateDomain.ORDER_LIFECYCLE,
                StateDomain.CONTROL_COMMANDS,
            ),
            emits_state=(StateDomain.IMMUTABLE_JOURNAL,),
            execution_mode="async",
            notes="Append-only audit log and replay source of truth.",
        ),
        _module(
            "monitoring",
            owns_state=(StateDomain.METRICS_SNAPSHOT,),
            reads_state=(
                StateDomain.RAW_MARKET_EVENTS,
                StateDomain.NORMALIZED_MARKET_STATE,
                StateDomain.STRATEGY_PROPOSALS,
                StateDomain.RISK_STATE,
                StateDomain.ACCOUNT_STATE,
                StateDomain.EXECUTION_INTENTS,
                StateDomain.ORDER_LIFECYCLE,
                StateDomain.IMMUTABLE_JOURNAL,
            ),
            emits_state=(StateDomain.METRICS_SNAPSHOT,),
            execution_mode="async",
            notes="Dashboards, alerts, and anomaly surfaces.",
        ),
        _module(
            "control_plane",
            owns_state=(StateDomain.CONTROL_COMMANDS,),
            reads_state=(StateDomain.ACCOUNT_STATE, StateDomain.RISK_STATE),
            emits_state=(StateDomain.CONTROL_COMMANDS,),
            execution_mode="sync",
            notes="Human override and kill-switch command boundary.",
        ),
        _module(
            "strategy_registry",
            owns_state=(StateDomain.STRATEGY_REGISTRY,),
            reads_state=(StateDomain.IMMUTABLE_JOURNAL,),
            emits_state=(StateDomain.STRATEGY_REGISTRY,),
            execution_mode="sync",
            notes="Versioned strategy catalog for future experimentation.",
        ),
    ),
)
