from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable

from .journal import EventType, JournalEvent
from .normalization import MarketAnomaly, MarketRegime, NormalizedMarketSnapshot, NORMALIZATION_SCHEMA_VERSION

RISK_SCHEMA_VERSION = "1.0.0"


class RiskDecisionStatus(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class RiskReason(StrEnum):
    STALE_MARKET_DATA = "stale_market_data"
    NORMALIZATION_VERSION_MISMATCH = "normalization_version_mismatch"
    SYMBOL_NOT_ALLOWED = "symbol_not_allowed"
    COOLDOWN_ACTIVE = "cooldown_active"
    INVALID_INTENT = "invalid_intent"
    INVALID_QUANTITY = "invalid_quantity"
    MISSING_LIMIT_PRICE = "missing_limit_price"
    EXCEEDS_MAX_ORDER_NOTIONAL = "exceeds_max_order_notional"
    EXCEEDS_MAX_POSITION_NOTIONAL = "exceeds_max_position_notional"
    DRAWDOWN_LIMIT_BREACHED = "drawdown_limit_breached"
    NON_POSITIVE_EQUITY = "non_positive_equity"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class TradeIntent:
    schema_version: str
    trade_id: str
    correlation_id: str
    symbol: str
    side: TradeSide
    order_type: str
    quantity: float
    limit_price: float | None
    strategy_id: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    schema_version: str
    allowed_symbols: tuple[str, ...]
    max_order_notional_quote: float
    max_position_notional_quote: float
    max_drawdown_pct: float
    cooldown_seconds: int
    enter_cooldown_on_veto: bool = True


@dataclass(frozen=True, slots=True)
class RiskState:
    schema_version: str
    cooldown_until: datetime | None
    peak_equity_quote: float
    last_evaluated_at: datetime | None
    last_decision_id: str | None


@dataclass(frozen=True, slots=True)
class RiskFinding:
    check_name: str
    passed: bool
    reason: RiskReason | None
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    schema_version: str
    decision_id: str
    status: RiskDecisionStatus
    reasons: tuple[RiskReason, ...]
    findings: tuple[RiskFinding, ...]
    intent: TradeIntent
    market_schema_version: str
    market_regime: MarketRegime
    evaluated_at: datetime
    risk_policy_version: str
    state_before: RiskState
    next_state: RiskState
    order_notional_quote: float
    position_notional_after_quote: float

    def to_journal_event(self) -> JournalEvent:
        return JournalEvent(
            event_id=self.decision_id,
            event_type=EventType.RISK_DECISION,
            schema_version=self.schema_version,
            source_module="risk_engine",
            occurred_at=self.evaluated_at,
            correlation_id=self.intent.correlation_id,
            symbol=self.intent.symbol,
            trade_id=self.intent.trade_id,
            payload={
                "decision_id": self.decision_id,
                "status": self.status.value,
                "reasons": [reason.value for reason in self.reasons],
                "findings": [
                    {
                        "check_name": finding.check_name,
                        "passed": finding.passed,
                        "reason": finding.reason.value if finding.reason else None,
                        "detail": finding.detail,
                    }
                    for finding in self.findings
                ],
                "intent": _intent_payload(self.intent),
                "market_schema_version": self.market_schema_version,
                "market_regime": self.market_regime.value,
                "risk_policy_version": self.risk_policy_version,
                "state_before": _state_payload(self.state_before),
                "next_state": _state_payload(self.next_state),
                "order_notional_quote": self.order_notional_quote,
                "position_notional_after_quote": self.position_notional_after_quote,
            },
        )

    @classmethod
    def from_journal_event(cls, event: JournalEvent) -> "RiskDecision":
        if event.event_type != EventType.RISK_DECISION:
            raise ValueError("event is not a risk decision")
        payload = event.payload or {}
        intent = _intent_from_payload(payload["intent"])
        state_before = _state_from_payload(payload["state_before"])
        next_state = _state_from_payload(payload["next_state"])
        findings = tuple(
            RiskFinding(
                check_name=str(item["check_name"]),
                passed=bool(item["passed"]),
                reason=RiskReason(item["reason"]) if item["reason"] is not None else None,
                detail=str(item["detail"]),
            )
            for item in payload.get("findings", [])
        )
        return cls(
            schema_version=str(event.schema_version),
            decision_id=str(payload["decision_id"]),
            status=RiskDecisionStatus(str(payload["status"])),
            reasons=tuple(RiskReason(reason) for reason in payload.get("reasons", [])),
            findings=findings,
            intent=intent,
            market_schema_version=str(payload["market_schema_version"]),
            market_regime=MarketRegime(str(payload["market_regime"])),
            evaluated_at=event.occurred_at,
            risk_policy_version=str(payload["risk_policy_version"]),
            state_before=state_before,
            next_state=next_state,
            order_notional_quote=float(payload["order_notional_quote"]),
            position_notional_after_quote=float(payload["position_notional_after_quote"]),
        )


class RiskEngine:
    def __init__(self, policy: RiskPolicy):
        self.policy = policy

    def evaluate(
        self,
        *,
        intent: TradeIntent,
        market: NormalizedMarketSnapshot,
        account_equity_quote: float,
        current_position_notional_quote: float,
        state: RiskState,
    ) -> RiskDecision:
        evaluated_at = market.observed_at.astimezone(timezone.utc)
        reasons: list[RiskReason] = []
        findings: list[RiskFinding] = []

        if state.cooldown_until is not None and evaluated_at < state.cooldown_until.astimezone(timezone.utc):
            reasons.append(RiskReason.COOLDOWN_ACTIVE)
            findings.append(
                RiskFinding(
                    check_name="cooldown",
                    passed=False,
                    reason=RiskReason.COOLDOWN_ACTIVE,
                    detail=f"cooldown_until={_fmt_dt(state.cooldown_until)}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="cooldown",
                    passed=True,
                    reason=None,
                    detail="no active cooldown",
                )
            )

        if market.schema_version != NORMALIZATION_SCHEMA_VERSION:
            reasons.append(RiskReason.NORMALIZATION_VERSION_MISMATCH)
            findings.append(
                RiskFinding(
                    check_name="normalization_version",
                    passed=False,
                    reason=RiskReason.NORMALIZATION_VERSION_MISMATCH,
                    detail=f"market_schema_version={market.schema_version}; expected={NORMALIZATION_SCHEMA_VERSION}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="normalization_version",
                    passed=True,
                    reason=None,
                    detail=f"market_schema_version={market.schema_version}",
                )
            )

        stale_market = market.regime == MarketRegime.STALE or MarketAnomaly.STALE_DATA in market.anomalies
        if stale_market:
            reasons.append(RiskReason.STALE_MARKET_DATA)
            findings.append(
                RiskFinding(
                    check_name="market_freshness",
                    passed=False,
                    reason=RiskReason.STALE_MARKET_DATA,
                    detail=f"regime={market.regime.value}; anomalies={[a.value for a in market.anomalies]}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="market_freshness",
                    passed=True,
                    reason=None,
                    detail=f"regime={market.regime.value}",
                )
            )

        if intent.symbol not in self.policy.allowed_symbols:
            reasons.append(RiskReason.SYMBOL_NOT_ALLOWED)
            findings.append(
                RiskFinding(
                    check_name="symbol_allowlist",
                    passed=False,
                    reason=RiskReason.SYMBOL_NOT_ALLOWED,
                    detail=f"symbol={intent.symbol}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="symbol_allowlist",
                    passed=True,
                    reason=None,
                    detail=f"symbol={intent.symbol}",
                )
            )

        if account_equity_quote <= 0:
            reasons.append(RiskReason.NON_POSITIVE_EQUITY)
            findings.append(
                RiskFinding(
                    check_name="account_equity",
                    passed=False,
                    reason=RiskReason.NON_POSITIVE_EQUITY,
                    detail=f"equity={account_equity_quote}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="account_equity",
                    passed=True,
                    reason=None,
                    detail=f"equity={account_equity_quote}",
                )
            )

        if not _is_finite_positive(intent.quantity):
            reasons.append(RiskReason.INVALID_QUANTITY)
            findings.append(
                RiskFinding(
                    check_name="quantity",
                    passed=False,
                    reason=RiskReason.INVALID_QUANTITY,
                    detail=f"quantity={intent.quantity}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="quantity",
                    passed=True,
                    reason=None,
                    detail=f"quantity={intent.quantity}",
                )
            )

        if intent.order_type.lower() == "limit" and intent.limit_price is None:
            reasons.append(RiskReason.MISSING_LIMIT_PRICE)
            findings.append(
                RiskFinding(
                    check_name="limit_price",
                    passed=False,
                    reason=RiskReason.MISSING_LIMIT_PRICE,
                    detail="limit order without limit_price",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="limit_price",
                    passed=True,
                    reason=None,
                    detail=f"order_type={intent.order_type}; limit_price={intent.limit_price}",
                )
            )

        reference_price = _reference_price(intent, market)
        order_notional_quote = intent.quantity * reference_price
        if order_notional_quote > self.policy.max_order_notional_quote:
            reasons.append(RiskReason.EXCEEDS_MAX_ORDER_NOTIONAL)
            findings.append(
                RiskFinding(
                    check_name="max_order_notional",
                    passed=False,
                    reason=RiskReason.EXCEEDS_MAX_ORDER_NOTIONAL,
                    detail=f"order_notional={order_notional_quote}; limit={self.policy.max_order_notional_quote}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="max_order_notional",
                    passed=True,
                    reason=None,
                    detail=f"order_notional={order_notional_quote}",
                )
            )

        position_notional_after = current_position_notional_quote + order_notional_quote
        if position_notional_after > self.policy.max_position_notional_quote:
            reasons.append(RiskReason.EXCEEDS_MAX_POSITION_NOTIONAL)
            findings.append(
                RiskFinding(
                    check_name="max_position_notional",
                    passed=False,
                    reason=RiskReason.EXCEEDS_MAX_POSITION_NOTIONAL,
                    detail=f"position_after={position_notional_after}; limit={self.policy.max_position_notional_quote}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="max_position_notional",
                    passed=True,
                    reason=None,
                    detail=f"position_after={position_notional_after}",
                )
            )

        drawdown = 0.0 if state.peak_equity_quote <= 0 else max((state.peak_equity_quote - account_equity_quote) / state.peak_equity_quote, 0.0)
        if drawdown > self.policy.max_drawdown_pct:
            reasons.append(RiskReason.DRAWDOWN_LIMIT_BREACHED)
            findings.append(
                RiskFinding(
                    check_name="drawdown",
                    passed=False,
                    reason=RiskReason.DRAWDOWN_LIMIT_BREACHED,
                    detail=f"drawdown={drawdown:.6f}; limit={self.policy.max_drawdown_pct:.6f}",
                )
            )
        else:
            findings.append(
                RiskFinding(
                    check_name="drawdown",
                    passed=True,
                    reason=None,
                    detail=f"drawdown={drawdown:.6f}",
                )
            )

        status = RiskDecisionStatus.APPROVED if not reasons else RiskDecisionStatus.DENIED
        decision_id = _decision_id(intent, market, state, status, reasons)
        if status == RiskDecisionStatus.APPROVED:
            next_state = RiskState(
                schema_version=state.schema_version,
                cooldown_until=None,
                peak_equity_quote=max(state.peak_equity_quote, account_equity_quote),
                last_evaluated_at=evaluated_at,
                last_decision_id=decision_id,
            )
        else:
            cooldown_until = state.cooldown_until
            if self.policy.enter_cooldown_on_veto:
                cooldown_until = evaluated_at + timedelta(seconds=self.policy.cooldown_seconds)
            next_state = RiskState(
                schema_version=state.schema_version,
                cooldown_until=cooldown_until,
                peak_equity_quote=max(state.peak_equity_quote, account_equity_quote),
                last_evaluated_at=evaluated_at,
                last_decision_id=decision_id,
            )

        return RiskDecision(
            schema_version=RISK_SCHEMA_VERSION,
            decision_id=decision_id,
            status=status,
            reasons=tuple(dict.fromkeys(reasons)),
            findings=tuple(findings),
            intent=intent,
            market_schema_version=market.schema_version,
            market_regime=market.regime,
            evaluated_at=evaluated_at,
            risk_policy_version=self.policy.schema_version,
            state_before=state,
            next_state=next_state,
            order_notional_quote=order_notional_quote,
            position_notional_after_quote=position_notional_after,
        )


def _reference_price(intent: TradeIntent, market: NormalizedMarketSnapshot) -> float:
    if intent.limit_price is not None:
        return float(intent.limit_price)
    if market.bars:
        return float(market.bars[-1].close)
    raise ValueError("cannot evaluate order notional without limit_price or market bar")


def _is_finite_positive(value: float) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in {float("inf"), float("-inf")} and float(value) > 0


def _decision_id(
    intent: TradeIntent,
    market: NormalizedMarketSnapshot,
    state: RiskState,
    status: RiskDecisionStatus,
    reasons: Iterable[RiskReason],
) -> str:
    canonical = json.dumps(
        {
            "intent": _intent_payload(intent),
            "market": {
                "schema_version": market.schema_version,
                "symbol": market.normalized_symbol,
                "regime": market.regime.value,
                "anomalies": [anomaly.value for anomaly in market.anomalies],
                "observed_at": _fmt_dt(market.observed_at),
            },
            "state_before": _state_payload(state),
            "status": status.value,
            "reasons": [reason.value for reason in reasons],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"risk-{digest}"


def _intent_payload(intent: TradeIntent) -> dict[str, object]:
    return {
        "schema_version": intent.schema_version,
        "trade_id": intent.trade_id,
        "correlation_id": intent.correlation_id,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "order_type": intent.order_type,
        "quantity": intent.quantity,
        "limit_price": intent.limit_price,
        "strategy_id": intent.strategy_id,
        "requested_at": _fmt_dt(intent.requested_at),
    }


def _intent_from_payload(payload: dict[str, object]) -> TradeIntent:
    return TradeIntent(
        schema_version=str(payload["schema_version"]),
        trade_id=str(payload["trade_id"]),
        correlation_id=str(payload["correlation_id"]),
        symbol=str(payload["symbol"]),
        side=TradeSide(str(payload["side"])),
        order_type=str(payload["order_type"]),
        quantity=float(payload["quantity"]),
        limit_price=None if payload.get("limit_price") is None else float(payload["limit_price"]),
        strategy_id=str(payload["strategy_id"]),
        requested_at=_parse_dt(str(payload["requested_at"])),
    )


def _state_payload(state: RiskState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "cooldown_until": None if state.cooldown_until is None else _fmt_dt(state.cooldown_until),
        "peak_equity_quote": state.peak_equity_quote,
        "last_evaluated_at": None if state.last_evaluated_at is None else _fmt_dt(state.last_evaluated_at),
        "last_decision_id": state.last_decision_id,
    }


def _state_from_payload(payload: dict[str, object]) -> RiskState:
    return RiskState(
        schema_version=str(payload["schema_version"]),
        cooldown_until=None if payload.get("cooldown_until") is None else _parse_dt(str(payload["cooldown_until"])),
        peak_equity_quote=float(payload["peak_equity_quote"]),
        last_evaluated_at=None if payload.get("last_evaluated_at") is None else _parse_dt(str(payload["last_evaluated_at"])),
        last_decision_id=None if payload.get("last_decision_id") is None else str(payload["last_decision_id"]),
    )


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
