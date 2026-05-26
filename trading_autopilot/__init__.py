"""Trading autopilot package.

Phase 0 task 0.1 defines the module boundaries and versioned contracts for the
modular monolith MVP.
"""

from .journal import (
    AppendOnlyJournal,
    EventType,
    JournalCorruptionError,
    JournalDuplicateEventError,
    JournalEvent,
    JournalError,
    ReplayContext,
    ReplayResult,
)
from .manifest import DEFAULT_MANIFEST, MANIFEST_SCHEMA_VERSION, ModuleContract, RuntimeManifest, RuntimeManifestError, StateDomain
from .normalization import (
    MarketAnomaly,
    MarketBar,
    MarketRegime,
    MarketSnapshot,
    MarketTick,
    NormalizationError,
    NORMALIZATION_SCHEMA_VERSION,
    NormalizedMarketSnapshot,
    normalize_market_snapshot,
)
from .risk import (
    RISK_SCHEMA_VERSION,
    RiskDecision,
    RiskDecisionStatus,
    RiskEngine,
    RiskFinding,
    RiskPolicy,
    RiskReason,
    RiskState,
    TradeIntent,
    TradeSide,
)
from .strategy import (
    STRATEGY_SCHEMA_VERSION,
    DeterministicStrategyProvider,
    ShadowLLMStrategyProvider,
    StrategyAction,
    StrategyProposal,
    StrategyProposalValidationError,
    StrategyRunContext,
)
from .monitoring import (
    AlertSeverity,
    AnomalyThrottle,
    MONITORING_SCHEMA_VERSION,
    MonitoringAlert,
    MonitoringMetric,
    MonitoringReport,
    MonitoringSection,
    MonitoringSignal,
    build_observer_monitoring_report,
    format_observer_monitoring_report,
    render_observer_monitoring_dashboard,
)

BOOT_REPORT_VERSION = "1.0.0"

_OBSERVER_EXPORTS = {
    "LIVE_ORDER_PATH_ENABLED",
    "OBSERVER_SCHEMA_VERSION",
    "ObserverRunner",
    "ObserverSessionResult",
    "ObserverSessionStep",
    "ShadowLedgerEntry",
    "ShadowPortfolio",
    "ShadowPosition",
    "SimulatedFillExecution",
    "SimulatedFillModel",
}

__all__ = [
    "AppendOnlyJournal",
    "BOOT_REPORT_VERSION",
    "DEFAULT_MANIFEST",
    "EventType",
    "JournalCorruptionError",
    "JournalDuplicateEventError",
    "JournalEvent",
    "JournalError",
    "LIVE_ORDER_PATH_ENABLED",
    "MANIFEST_SCHEMA_VERSION",
    "MarketAnomaly",
    "MarketBar",
    "MarketRegime",
    "MarketSnapshot",
    "MarketTick",
    "ModuleContract",
    "NORMALIZATION_SCHEMA_VERSION",
    "NormalizedMarketSnapshot",
    "NormalizationError",
    "OBSERVER_SCHEMA_VERSION",
    "ObserverRunner",
    "ObserverSessionResult",
    "ObserverSessionStep",
    "STRATEGY_SCHEMA_VERSION",
    "StrategyAction",
    "StrategyProposal",
    "StrategyProposalValidationError",
    "StrategyRunContext",
    "RISK_SCHEMA_VERSION",
    "AlertSeverity",
    "AnomalyThrottle",
    "ReplayContext",
    "ReplayResult",
    "RiskDecision",
    "RiskDecisionStatus",
    "RiskEngine",
    "RiskFinding",
    "RiskPolicy",
    "RiskReason",
    "RiskState",
    "RuntimeManifest",
    "RuntimeManifestError",
    "ShadowLedgerEntry",
    "ShadowPortfolio",
    "ShadowPosition",
    "SimulatedFillExecution",
    "SimulatedFillModel",
    "StateDomain",
    "TradeIntent",
    "TradeSide",
    "MONITORING_SCHEMA_VERSION",
    "AlertSeverity",
    "AnomalyThrottle",
    "MonitoringAlert",
    "MonitoringMetric",
    "MonitoringReport",
    "MonitoringSection",
    "MonitoringSignal",
    "build_observer_monitoring_report",
    "format_observer_monitoring_report",
    "render_observer_monitoring_dashboard",
    "normalize_market_snapshot",
]


def __getattr__(name: str):
    if name in _OBSERVER_EXPORTS:
        from .observer import (
            LIVE_ORDER_PATH_ENABLED,
            OBSERVER_SCHEMA_VERSION,
            ObserverRunner,
            ObserverSessionResult,
            ObserverSessionStep,
            ShadowLedgerEntry,
            ShadowPortfolio,
            ShadowPosition,
            SimulatedFillExecution,
            SimulatedFillModel,
        )

        mapping = {
            "LIVE_ORDER_PATH_ENABLED": LIVE_ORDER_PATH_ENABLED,
            "OBSERVER_SCHEMA_VERSION": OBSERVER_SCHEMA_VERSION,
            "ObserverRunner": ObserverRunner,
            "ObserverSessionResult": ObserverSessionResult,
            "ObserverSessionStep": ObserverSessionStep,
            "ShadowLedgerEntry": ShadowLedgerEntry,
            "ShadowPortfolio": ShadowPortfolio,
            "ShadowPosition": ShadowPosition,
            "SimulatedFillExecution": SimulatedFillExecution,
            "SimulatedFillModel": SimulatedFillModel,
        }
        return mapping[name]
    raise AttributeError(name)
