from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Protocol

from .journal import EventType, JournalEvent
from .normalization import MarketRegime, NormalizedMarketSnapshot
from .risk import TradeIntent, TradeSide

STRATEGY_SCHEMA_VERSION = "1.0.0"


class StrategyProposalValidationError(ValueError):
    pass


class StrategyAction(StrEnum):
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class StrategyRunContext:
    schema_version: str
    session_id: str
    correlation_id: str
    strategy_id: str
    strategy_version: str
    prompt_version: str
    model_name: str
    model_version: str
    normalization_version: str
    risk_version: str
    market: NormalizedMarketSnapshot
    portfolio_cash_quote: float
    portfolio_equity_quote: float
    position_notional_quote: float


@dataclass(frozen=True, slots=True)
class StrategyProposal:
    schema_version: str
    trade_id: str
    session_id: str
    correlation_id: str
    strategy_id: str
    strategy_version: str
    prompt_version: str
    model_name: str
    model_version: str
    source_kind: str
    normalization_version: str
    risk_version: str
    market_schema_version: str
    market_symbol: str
    market_regime: MarketRegime
    generated_at: datetime
    action: StrategyAction
    order_type: str
    quantity: float
    limit_price: float | None
    rationale: str
    confidence: float | None
    portfolio_cash_quote: float
    portfolio_equity_quote: float
    position_notional_quote: float

    @property
    def proposal_id(self) -> str:
        return self.trade_id

    def to_trade_intent(self) -> TradeIntent:
        if self.action == StrategyAction.HOLD:
            raise ValueError("hold proposals do not convert to trade intents")
        side = TradeSide.BUY if self.action == StrategyAction.BUY else TradeSide.SELL
        return TradeIntent(
            schema_version=self.schema_version,
            trade_id=self.trade_id,
            correlation_id=self.correlation_id,
            symbol=self.market_symbol,
            side=side,
            order_type=self.order_type,
            quantity=self.quantity,
            limit_price=self.limit_price,
            strategy_id=self.strategy_id,
            requested_at=self.generated_at,
        )

    def to_journal_event(self) -> JournalEvent:
        return JournalEvent(
            event_id=f"strategy-{self.trade_id}",
            event_type=EventType.STRATEGY_PROPOSAL,
            schema_version=self.schema_version,
            source_module="strategy_layer",
            occurred_at=self.generated_at,
            correlation_id=self.correlation_id,
            symbol=self.market_symbol,
            trade_id=self.trade_id,
            payload={
                "proposal_id": self.proposal_id,
                "trade_id": self.trade_id,
                "schema_version": self.schema_version,
                "session_id": self.session_id,
                "correlation_id": self.correlation_id,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "prompt_version": self.prompt_version,
                "model_name": self.model_name,
                "model_version": self.model_version,
                "source_kind": self.source_kind,
                "normalization_version": self.normalization_version,
                "risk_version": self.risk_version,
                "market_schema_version": self.market_schema_version,
                "market_symbol": self.market_symbol,
                "market_regime": self.market_regime.value,
                "generated_at": _fmt_dt(self.generated_at),
                "action": self.action.value,
                "order_type": self.order_type,
                "quantity": self.quantity,
                "limit_price": self.limit_price,
                "rationale": self.rationale,
                "confidence": self.confidence,
                "portfolio_cash_quote": self.portfolio_cash_quote,
                "portfolio_equity_quote": self.portfolio_equity_quote,
                "position_notional_quote": self.position_notional_quote,
            },
        )

    @classmethod
    def from_journal_event(cls, event: JournalEvent) -> "StrategyProposal":
        if event.event_type != EventType.STRATEGY_PROPOSAL:
            raise ValueError("event is not a strategy proposal")
        payload = event.payload or {}
        return cls(
            schema_version=str(payload.get("schema_version", event.schema_version)),
            trade_id=str(payload.get("trade_id", payload.get("proposal_id", event.event_id))),
            session_id=str(payload["session_id"]),
            correlation_id=str(payload["correlation_id"]),
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(payload["strategy_version"]),
            prompt_version=str(payload["prompt_version"]),
            model_name=str(payload["model_name"]),
            model_version=str(payload["model_version"]),
            source_kind=str(payload["source_kind"]),
            normalization_version=str(payload["normalization_version"]),
            risk_version=str(payload["risk_version"]),
            market_schema_version=str(payload["market_schema_version"]),
            market_symbol=str(payload["market_symbol"]),
            market_regime=MarketRegime(str(payload["market_regime"])),
            generated_at=event.occurred_at,
            action=StrategyAction(str(payload["action"])),
            order_type=str(payload["order_type"]),
            quantity=float(payload["quantity"]),
            limit_price=None if payload.get("limit_price") is None else float(payload["limit_price"]),
            rationale=str(payload["rationale"]),
            confidence=None if payload.get("confidence") is None else float(payload["confidence"]),
            portfolio_cash_quote=float(payload["portfolio_cash_quote"]),
            portfolio_equity_quote=float(payload["portfolio_equity_quote"]),
            position_notional_quote=float(payload["position_notional_quote"]),
        )

    @classmethod
    def from_trade_intent(
        cls,
        intent: TradeIntent,
        *,
        context: StrategyRunContext,
        source_kind: str = "manual",
        rationale: str = "direct intent fallback",
        confidence: float | None = 1.0,
    ) -> "StrategyProposal":
        action = StrategyAction.BUY if intent.side == TradeSide.BUY else StrategyAction.SELL
        return cls._build(
            context=context,
            source_kind=source_kind,
            action=action,
            order_type=intent.order_type,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            rationale=rationale,
            confidence=confidence,
            trade_id=intent.trade_id,
        )

    @classmethod
    def from_raw(
        cls,
        raw: object,
        *,
        context: StrategyRunContext,
        source_kind: str = "llm",
    ) -> "StrategyProposal":
        if isinstance(raw, StrategyProposal):
            return raw
        payload = _coerce_payload(raw)
        return cls._build(
            context=context,
            source_kind=source_kind,
            action=_parse_action(payload),
            order_type=_parse_order_type(payload),
            quantity=_parse_quantity(payload),
            limit_price=_parse_limit_price(payload),
            rationale=_parse_rationale(payload),
            confidence=_parse_confidence(payload),
        )

    @classmethod
    def _build(
        cls,
        *,
        context: StrategyRunContext,
        source_kind: str,
        action: StrategyAction,
        order_type: str,
        quantity: float,
        limit_price: float | None,
        rationale: str,
        confidence: float | None,
        trade_id: str | None = None,
    ) -> "StrategyProposal":
        normalized_order_type = order_type.strip().lower()
        if action == StrategyAction.HOLD:
            if normalized_order_type != "hold":
                raise StrategyProposalValidationError("hold proposals must use order_type='hold'")
            if quantity != 0:
                raise StrategyProposalValidationError("hold proposals must use quantity=0")
            if limit_price is not None:
                raise StrategyProposalValidationError("hold proposals must not set limit_price")
        else:
            if normalized_order_type not in {"market", "limit"}:
                raise StrategyProposalValidationError("order_type must be 'market' or 'limit' for active proposals")
            if quantity <= 0:
                raise StrategyProposalValidationError("active proposals must use a positive quantity")
            if normalized_order_type == "limit" and limit_price is None:
                raise StrategyProposalValidationError("limit proposals require limit_price")
            if normalized_order_type == "market" and limit_price is not None:
                raise StrategyProposalValidationError("market proposals must not set limit_price")
        if not rationale:
            raise StrategyProposalValidationError("rationale must be set")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise StrategyProposalValidationError("confidence must be between 0 and 1")
        generated_at = context.market.observed_at.astimezone(timezone.utc)
        proposal_payload = {
            "context": {
                "schema_version": context.schema_version,
                "session_id": context.session_id,
                "correlation_id": context.correlation_id,
                "strategy_id": context.strategy_id,
                "strategy_version": context.strategy_version,
                "prompt_version": context.prompt_version,
                "model_name": context.model_name,
                "model_version": context.model_version,
                "normalization_version": context.normalization_version,
                "risk_version": context.risk_version,
                "market_schema_version": context.market.schema_version,
                "market_symbol": context.market.normalized_symbol,
                "market_regime": context.market.regime.value,
                "generated_at": _fmt_dt(generated_at),
                "portfolio_cash_quote": context.portfolio_cash_quote,
                "portfolio_equity_quote": context.portfolio_equity_quote,
                "position_notional_quote": context.position_notional_quote,
            },
            "proposal": {
                "action": action.value,
                "order_type": normalized_order_type,
                "quantity": float(quantity),
                "limit_price": None if limit_price is None else float(limit_price),
                "rationale": rationale,
                "confidence": confidence,
                "source_kind": source_kind,
            },
        }
        if trade_id is None:
            trade_id = _trade_id_from_payload(proposal_payload)
        return cls(
            schema_version=context.schema_version,
            trade_id=trade_id,
            session_id=context.session_id,
            correlation_id=context.correlation_id,
            strategy_id=context.strategy_id,
            strategy_version=context.strategy_version,
            prompt_version=context.prompt_version,
            model_name=context.model_name,
            model_version=context.model_version,
            source_kind=source_kind,
            normalization_version=context.normalization_version,
            risk_version=context.risk_version,
            market_schema_version=context.market.schema_version,
            market_symbol=context.market.normalized_symbol,
            market_regime=context.market.regime,
            generated_at=generated_at,
            action=action,
            order_type=normalized_order_type,
            quantity=float(quantity),
            limit_price=None if limit_price is None else float(limit_price),
            rationale=rationale,
            confidence=confidence,
            portfolio_cash_quote=context.portfolio_cash_quote,
            portfolio_equity_quote=context.portfolio_equity_quote,
            position_notional_quote=context.position_notional_quote,
        )


class StrategyProvider(Protocol):
    source_kind: str

    def propose(self, context: StrategyRunContext) -> object:
        ...


class ShadowLLMStrategyProvider:
    def __init__(
        self,
        generate_raw: Callable[[StrategyRunContext], object],
        *,
        source_kind: str = "llm",
        strategy_id: str = "llm-shadow-strategy",
        strategy_version: str = "1.0.0",
        prompt_version: str = "shadow-v1",
        model_name: str = "gpt-5.5",
        model_version: str = "xhigh",
    ):
        self.generate_raw = generate_raw
        self.source_kind = source_kind
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.prompt_version = prompt_version
        self.model_name = model_name
        self.model_version = model_version

    def propose(self, context: StrategyRunContext) -> StrategyProposal:
        raw = self.generate_raw(context)
        return StrategyProposal.from_raw(raw, context=context, source_kind=self.source_kind)


class DeterministicStrategyProvider:
    source_kind = "fake"
    strategy_id = "deterministic-shadow-strategy"
    strategy_version = "1.0.0"
    prompt_version = "deterministic-v1"
    model_name = "gpt-5.5"
    model_version = "xhigh"

    def propose(self, context: StrategyRunContext) -> StrategyProposal:
        market = context.market
        base_price = _reference_price(market)
        if market.regime in {MarketRegime.UPTREND, MarketRegime.RANGING}:
            return StrategyProposal.from_raw(
                {
                    "action": "buy",
                    "order_type": "limit",
                    "quantity": 0.5,
                    "limit_price": base_price,
                    "rationale": f"deterministic demo: regime={market.regime.value}",
                    "confidence": 0.61,
                },
                context=context,
                source_kind=self.source_kind,
            )
        return StrategyProposal.from_raw(
            {
                "action": "hold",
                "order_type": "hold",
                "quantity": 0.0,
                "limit_price": None,
                "rationale": f"deterministic demo: regime={market.regime.value}",
                "confidence": 0.5,
            },
            context=context,
            source_kind=self.source_kind,
        )


def _coerce_payload(raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StrategyProposalValidationError("strategy output must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise StrategyProposalValidationError("strategy output must be a mapping or JSON object")
    return raw


def _parse_action(payload: dict[str, object]) -> StrategyAction:
    try:
        return StrategyAction(str(payload["action"]).strip().lower())
    except KeyError as exc:
        raise StrategyProposalValidationError("missing strategy action") from exc
    except ValueError as exc:
        raise StrategyProposalValidationError("invalid strategy action") from exc


def _parse_order_type(payload: dict[str, object]) -> str:
    try:
        value = str(payload["order_type"]).strip().lower()
    except KeyError as exc:
        raise StrategyProposalValidationError("missing order_type") from exc
    if value not in {"hold", "market", "limit"}:
        raise StrategyProposalValidationError("invalid order_type")
    return value


def _parse_quantity(payload: dict[str, object]) -> float:
    try:
        quantity = float(payload["quantity"])
    except KeyError as exc:
        raise StrategyProposalValidationError("missing quantity") from exc
    except (TypeError, ValueError) as exc:
        raise StrategyProposalValidationError("quantity must be numeric") from exc
    if not _is_finite(quantity):
        raise StrategyProposalValidationError("quantity must be finite")
    return quantity


def _parse_limit_price(payload: dict[str, object]) -> float | None:
    if payload.get("limit_price") is None:
        return None
    try:
        limit_price = float(payload["limit_price"])
    except (TypeError, ValueError) as exc:
        raise StrategyProposalValidationError("limit_price must be numeric") from exc
    if not _is_finite(limit_price):
        raise StrategyProposalValidationError("limit_price must be finite")
    return limit_price


def _parse_rationale(payload: dict[str, object]) -> str:
    try:
        rationale = str(payload["rationale"]).strip()
    except KeyError as exc:
        raise StrategyProposalValidationError("missing rationale") from exc
    if not rationale:
        raise StrategyProposalValidationError("rationale must be non-empty")
    return rationale


def _parse_confidence(payload: dict[str, object]) -> float | None:
    if payload.get("confidence") is None:
        return None
    try:
        confidence = float(payload["confidence"])
    except (TypeError, ValueError) as exc:
        raise StrategyProposalValidationError("confidence must be numeric") from exc
    if not _is_finite(confidence):
        raise StrategyProposalValidationError("confidence must be finite")
    return confidence


def _trade_id_from_payload(payload: dict[str, object]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:20]
    return f"trade-{digest}"


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def _reference_price(market: NormalizedMarketSnapshot) -> float:
    if market.bars:
        return float(market.bars[-1].close)
    return float(market.source_ticks[-1].price)


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
