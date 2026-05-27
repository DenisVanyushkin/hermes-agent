from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import sqlite3
from typing import Any, Iterable
from urllib.parse import urlparse, urlencode

import requests

from .journal import AppendOnlyJournal, EventType, JournalError, JournalEvent
from .normalization import MarketAnomaly, MarketSnapshot, MarketTick, NormalizedMarketSnapshot, normalize_market_snapshot

LIVE_READ_SCHEMA_VERSION = "1.0.0"
LIVE_READ_OPERATIONAL_MODE = "live_read_only"
LIVE_READ_SOURCE_KIND = "exchange_live_read"
DEFAULT_BINANCE_BASE_URL = "https://api.binance.com"
DEFAULT_BINANCE_TIMEOUT_SECONDS = 10.0


class LiveReadOnlyError(RuntimeError):
    """Raised when a write-capable exchange action is attempted in live-read-only mode."""


class LiveReadOnlySessionError(RuntimeError):
    """Raised when a live read session cannot be completed safely."""


class BinanceApiError(RuntimeError):
    """Raised when Binance returns an error response or an invalid payload."""


@dataclass(frozen=True, slots=True)
class LiveAccountBalance:
    asset: str
    free: float
    locked: float

    def to_dict(self, *, redact_values: bool = False) -> dict[str, object]:
        return {
            "asset": self.asset,
            "free": "<redacted>" if redact_values else self.free,
            "locked": "<redacted>" if redact_values else self.locked,
        }


@dataclass(frozen=True, slots=True)
class LiveAccountSnapshot:
    schema_version: str
    can_trade: bool
    can_withdraw: bool
    can_deposit: bool
    permissions: tuple[str, ...]
    balances: tuple[LiveAccountBalance, ...]

    def to_dict(self, *, redact_values: bool = False) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "can_trade": self.can_trade,
            "can_withdraw": self.can_withdraw,
            "can_deposit": self.can_deposit,
            "permissions": list(self.permissions),
            "balances": [balance.to_dict(redact_values=redact_values) for balance in self.balances],
        }


@dataclass(frozen=True, slots=True)
class LiveSymbolMetadata:
    schema_version: str
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    base_asset_precision: int | None
    quote_asset_precision: int | None
    price_precision: int | None
    quantity_precision: int | None
    tick_size: float | None
    step_size: float | None
    min_qty: float | None
    min_notional_quote: float | None
    order_types: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "status": self.status,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "base_asset_precision": self.base_asset_precision,
            "quote_asset_precision": self.quote_asset_precision,
            "price_precision": self.price_precision,
            "quantity_precision": self.quantity_precision,
            "tick_size": self.tick_size,
            "step_size": self.step_size,
            "min_qty": self.min_qty,
            "min_notional_quote": self.min_notional_quote,
            "order_types": list(self.order_types),
        }


@dataclass(frozen=True, slots=True)
class LiveMarketObservation:
    schema_version: str
    symbol: str
    ticker_price_text: str
    ticker_price_quote: float
    observed_at: datetime
    source_kind: str
    raw_ticker: dict[str, object]
    normalized_market: NormalizedMarketSnapshot

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "ticker_price_text": self.ticker_price_text,
            "ticker_price_quote": self.ticker_price_quote,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_kind": self.source_kind,
            "raw_ticker": self.raw_ticker,
            "normalized_market": self.normalized_market.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class LiveReadOnlyAlert:
    severity: str
    code: str
    title: str
    message: str
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class LiveExecutionProof:
    order_endpoints_called: bool
    blocked_methods: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "order_endpoints_called": self.order_endpoints_called,
            "blocked_methods": list(self.blocked_methods),
        }


@dataclass(frozen=True, slots=True)
class LiveReadOnlySessionRequest:
    session_id: str
    symbol: str
    journal: AppendOnlyJournal
    client: "BinanceLiveReadOnlyClient"
    observed_at: datetime | None = None
    include_metadata: bool = True


@dataclass(frozen=True, slots=True)
class LiveReadOnlyReport:
    schema_version: str
    session_id: str
    journal_path: str
    operational_mode: str
    execution_enabled: bool
    source_kind: str
    status: str
    failure_reason: str | None
    generated_at: datetime
    account: LiveAccountSnapshot | None
    symbol_metadata: LiveSymbolMetadata | None
    market: LiveMarketObservation | None
    normalized_market: NormalizedMarketSnapshot | None
    events: tuple[JournalEvent, ...]
    alerts: tuple[LiveReadOnlyAlert, ...]
    live_execution_proof: LiveExecutionProof
    redaction_mode: str = "none"

    def to_dict(self, *, redact_sensitive: bool = False) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "operational_mode": self.operational_mode,
            "execution_enabled": self.execution_enabled,
            "source_kind": self.source_kind,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "generated_at": self.generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "account": None if self.account is None else self.account.to_dict(redact_values=redact_sensitive),
            "symbol_metadata": None if self.symbol_metadata is None else self.symbol_metadata.to_dict(),
            "market": None if self.market is None else self.market.to_dict(),
            "normalized_market": None if self.normalized_market is None else self.normalized_market.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "live_execution_proof": self.live_execution_proof.to_dict(),
            "redaction_mode": "redacted" if redact_sensitive else self.redaction_mode,
        }


class _ReadOnlyTransportProxy:
    __slots__ = ("_request_fn", "order_endpoints_called", "blocked_methods")

    def __init__(self, session: requests.Session):
        self._request_fn = session.request
        self.order_endpoints_called = False
        self.blocked_methods: list[str] = []

    def request(self, method: str, url: str, *args: object, **kwargs: object):
        method_upper = method.upper()
        path = urlparse(url).path
        if _is_write_method(method_upper) or _is_write_path(path):
            self.order_endpoints_called = True
            blocked = f"{method_upper} {path}"
            self.blocked_methods.append(blocked)
            raise LiveReadOnlyError(f"live write endpoint blocked in Task 2.1: {blocked}")
        return self._request_fn(method, url, *args, **kwargs)


class BinanceLiveReadOnlyClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = DEFAULT_BINANCE_BASE_URL,
        session: requests.Session | None = None,
        timeout_seconds: float = DEFAULT_BINANCE_TIMEOUT_SECONDS,
    ):
        if not api_key:
            raise ValueError("api_key must be provided")
        if not api_secret:
            raise ValueError("api_secret must be provided")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._transport = _ReadOnlyTransportProxy(session or requests.Session())
        self.timeout_seconds = timeout_seconds

    @property
    def session(self) -> _ReadOnlyTransportProxy:
        return self._transport

    @property
    def order_endpoints_called(self) -> bool:
        return self._transport.order_endpoints_called

    @property
    def blocked_methods(self) -> tuple[str, ...]:
        return tuple(self._transport.blocked_methods)

    def submit_order(self, *args: object, **kwargs: object) -> None:
        self._blocked_write("submit_order")

    def cancel_order(self, *args: object, **kwargs: object) -> None:
        self._blocked_write("cancel_order")

    def get_account_snapshot(self) -> LiveAccountSnapshot:
        payload = self._signed_get("/api/v3/account")
        balances_raw = payload.get("balances")
        if not isinstance(balances_raw, list):
            raise LiveReadOnlySessionError("account response missing balances")
        balances_list: list[LiveAccountBalance] = []
        for balance in balances_raw:
            if not isinstance(balance, dict):
                raise LiveReadOnlySessionError("account response balances must contain objects")
            asset = balance.get("asset")
            if asset is None:
                raise LiveReadOnlySessionError("account response balance missing asset")
            balances_list.append(
                LiveAccountBalance(
                    asset=str(asset),
                    free=_to_float(balance.get("free", 0.0)),
                    locked=_to_float(balance.get("locked", 0.0)),
                )
            )
        balances = tuple(balances_list)
        permissions_raw = payload.get("permissions") or []
        if not isinstance(permissions_raw, list):
            raise LiveReadOnlySessionError("account response permissions must be a list")
        return LiveAccountSnapshot(
            schema_version=LIVE_READ_SCHEMA_VERSION,
            can_trade=bool(payload.get("canTrade", False)),
            can_withdraw=bool(payload.get("canWithdraw", False)),
            can_deposit=bool(payload.get("canDeposit", False)),
            permissions=tuple(str(permission) for permission in permissions_raw),
            balances=balances,
        )

    def get_ticker_price(self, symbol: str) -> dict[str, object]:
        payload = self._public_get("/api/v3/ticker/price", params={"symbol": symbol})
        price = payload.get("price")
        if price is None:
            raise LiveReadOnlySessionError("ticker response missing price")
        if payload.get("symbol") not in (None, symbol):
            raise LiveReadOnlySessionError(f"ticker symbol mismatch: requested={symbol} returned={payload.get('symbol')}")
        return payload

    def get_symbol_metadata(self, symbol: str) -> LiveSymbolMetadata:
        payload = self._public_get("/api/v3/exchangeInfo", params={"symbol": symbol})
        symbols = payload.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise LiveReadOnlySessionError(f"symbol unavailable: {symbol}")
        entry = symbols[0]
        if not isinstance(entry, dict):
            raise LiveReadOnlySessionError("exchangeInfo returned malformed symbol metadata")
        returned_symbol = str(entry.get("symbol", symbol))
        if returned_symbol != symbol:
            raise LiveReadOnlySessionError(f"exchangeInfo symbol mismatch: requested={symbol} returned={returned_symbol}")
        filters = {str(item.get("filterType")): item for item in entry.get("filters", []) if isinstance(item, dict)}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_size = filters.get("LOT_SIZE", {})
        min_notional = filters.get("MIN_NOTIONAL", {})
        tick_size = _optional_float(price_filter.get("tickSize"))
        step_size = _optional_float(lot_size.get("stepSize"))
        quote_asset_precision = _optional_int(entry.get("quoteAssetPrecision"))
        quote_precision = _optional_int(entry.get("quotePrecision"))
        return LiveSymbolMetadata(
            schema_version=LIVE_READ_SCHEMA_VERSION,
            symbol=returned_symbol,
            status=str(entry.get("status", "UNKNOWN")),
            base_asset=str(entry.get("baseAsset", "")),
            quote_asset=str(entry.get("quoteAsset", "")),
            base_asset_precision=_optional_int(entry.get("baseAssetPrecision")),
            quote_asset_precision=quote_asset_precision if quote_asset_precision is not None else quote_precision,
            price_precision=_precision_from_step(tick_size) if tick_size is not None else (quote_precision if quote_precision is not None else quote_asset_precision),
            quantity_precision=_precision_from_step(step_size) if step_size is not None else _optional_int(entry.get("baseAssetPrecision")),
            tick_size=tick_size,
            step_size=step_size,
            min_qty=_optional_float(lot_size.get("minQty")),
            min_notional_quote=_optional_float(min_notional.get("minNotional")),
            order_types=tuple(str(order_type) for order_type in entry.get("orderTypes", []) or []),
        )

    def _public_get(self, path: str, *, params: dict[str, object] | None = None) -> dict[str, object]:
        return self._request_json("GET", path, params=params)

    def _signed_get(self, path: str, *, params: dict[str, object] | None = None) -> dict[str, object]:
        signed_params = dict(params or {})
        signed_params["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        signed_params.setdefault("recvWindow", 5000)
        ordered_params = sorted(signed_params.items())
        query = urlencode(ordered_params)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), sha256).hexdigest()
        ordered_params.append(("signature", signature))
        return self._request_json(
            "GET",
            path,
            params=ordered_params,
            headers={"X-MBX-APIKEY": self.api_key},
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        url = f"{self.base_url}{path}"
        try:
            response = self._transport.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except LiveReadOnlyError:
            raise
        except requests.RequestException as exc:
            raise BinanceApiError(f"Binance request failed for {path}: {exc}") from exc
        except Exception as exc:
            raise BinanceApiError(f"Binance request failed for {path}: {exc}") from exc
        try:
            response.raise_for_status()
        except Exception as exc:
            message = getattr(response, "text", "")
            raise BinanceApiError(f"Binance request failed for {path}: {message or exc}") from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise BinanceApiError(f"Binance response for {path} was not JSON") from exc
        if not isinstance(payload, dict):
            raise BinanceApiError(f"Binance response for {path} must be a JSON object")
        return payload

    def _blocked_write(self, method_name: str) -> None:
        self._transport.order_endpoints_called = True
        self._transport.blocked_methods.append(method_name)
        raise LiveReadOnlyError(f"{method_name} is disabled in Task 2.1")


def build_live_read_only_report(request: LiveReadOnlySessionRequest) -> LiveReadOnlyReport:
    if not request.session_id or not request.session_id.strip():
        raise ValueError("session_id must be set")
    if not request.symbol or not request.symbol.strip():
        raise ValueError("symbol must be set")
    observed_at = _require_timezone_aware(request.observed_at or datetime.now(timezone.utc), name="observed_at")
    session_start_at = observed_at - timedelta(microseconds=1)
    session_end_at = observed_at + timedelta(microseconds=1)
    events: list[JournalEvent] = []
    alerts: list[LiveReadOnlyAlert] = []
    account: LiveAccountSnapshot | None = None
    symbol_metadata: LiveSymbolMetadata | None = None
    market: LiveMarketObservation | None = None
    normalized_market: NormalizedMarketSnapshot | None = None
    status = "ok"
    failure_reason: str | None = None

    def append_event(event: JournalEvent) -> None:
        try:
            request.journal.append(event)
        except Exception as exc:
            raise LiveReadOnlySessionError(f"journal write failed for {event.event_type.value}: {exc}") from exc
        events.append(event)

    try:
        append_event(
            _session_event(
                event_type=EventType.LIVE_READ_SESSION_START,
                session_id=request.session_id,
                suffix="start",
                occurred_at=session_start_at,
                payload={
                    "schema_version": LIVE_READ_SCHEMA_VERSION,
                    "operational_mode": LIVE_READ_OPERATIONAL_MODE,
                    "execution_enabled": False,
                    "source_kind": LIVE_READ_SOURCE_KIND,
                    "symbol": request.symbol,
                    "base_url": _redacted_base_url(request.client.base_url),
                },
            )
        )

        account = request.client.get_account_snapshot()
        append_event(
            _session_event(
                event_type=EventType.LIVE_READ_ACCOUNT_SNAPSHOT,
                session_id=request.session_id,
                suffix="account",
                occurred_at=observed_at,
                symbol=request.symbol,
                payload={
                    "schema_version": LIVE_READ_SCHEMA_VERSION,
                    "operational_mode": LIVE_READ_OPERATIONAL_MODE,
                    "execution_enabled": False,
                    "source_kind": LIVE_READ_SOURCE_KIND,
                    "account": account.to_dict(redact_values=True),
                    "account_summary": {
                        "can_trade": account.can_trade,
                        "can_withdraw": account.can_withdraw,
                        "can_deposit": account.can_deposit,
                        "permission_count": len(account.permissions),
                        "balance_count": len(account.balances),
                    },
                },
            )
        )

        ticker = request.client.get_ticker_price(request.symbol)
        raw_price_text = str(ticker["price"])
        price_quote = _to_float(raw_price_text)
        market_snapshot = MarketSnapshot(
            symbol=request.symbol,
            observed_at=observed_at,
            ticks=(
                MarketTick(
                    symbol=request.symbol,
                    observed_at=observed_at,
                    price=price_quote,
                    volume=0.0,
                    source=LIVE_READ_SOURCE_KIND,
                ),
            ),
        )
        normalized_market = normalize_market_snapshot(market_snapshot)
        market = LiveMarketObservation(
            schema_version=LIVE_READ_SCHEMA_VERSION,
            symbol=request.symbol,
            ticker_price_text=raw_price_text,
            ticker_price_quote=price_quote,
            observed_at=observed_at,
            source_kind=LIVE_READ_SOURCE_KIND,
            raw_ticker=ticker,
            normalized_market=normalized_market,
        )

        if request.include_metadata:
            symbol_metadata = request.client.get_symbol_metadata(request.symbol)
            append_event(
                _session_event(
                    event_type=EventType.LIVE_READ_SYMBOL_METADATA,
                    session_id=request.session_id,
                    suffix="metadata",
                    occurred_at=observed_at,
                    symbol=request.symbol,
                    payload={
                        "schema_version": LIVE_READ_SCHEMA_VERSION,
                        "operational_mode": LIVE_READ_OPERATIONAL_MODE,
                        "execution_enabled": False,
                        "source_kind": LIVE_READ_SOURCE_KIND,
                        "symbol_metadata": symbol_metadata.to_dict(),
                        "precision_summary": {
                            "tick_size": symbol_metadata.tick_size,
                            "step_size": symbol_metadata.step_size,
                            "min_qty": symbol_metadata.min_qty,
                            "min_notional_quote": symbol_metadata.min_notional_quote,
                        },
                    },
                )
            )
            if symbol_metadata.tick_size is not None and not _price_matches_tick(raw_price_text, symbol_metadata.tick_size):
                alerts.append(
                    LiveReadOnlyAlert(
                        severity="warning",
                        code="precision-mismatch",
                        title="Precision mismatch",
                        message="live ticker price is not aligned to the exchange tick size",
                        details={
                            "symbol": request.symbol,
                            "tick_size": symbol_metadata.tick_size,
                            "ticker_price_text": raw_price_text,
                            "normalized_price_quote": normalized_market.bars[-1].close if normalized_market.bars else price_quote,
                        },
                    )
                )

        if MarketAnomaly.STALE_DATA in normalized_market.anomalies:
            alerts.append(
                LiveReadOnlyAlert(
                    severity="warning",
                    code="stale-data",
                    title="Stale market data",
                    message="live market snapshot is stale",
                    details={"symbol": request.symbol, "observed_at": observed_at.isoformat()},
                )
            )

        append_event(
            _session_event(
                event_type=EventType.LIVE_READ_MARKET_SNAPSHOT,
                session_id=request.session_id,
                suffix="market",
                occurred_at=observed_at,
                symbol=request.symbol,
                payload={
                    "schema_version": LIVE_READ_SCHEMA_VERSION,
                    "operational_mode": LIVE_READ_OPERATIONAL_MODE,
                    "execution_enabled": False,
                    "source_kind": LIVE_READ_SOURCE_KIND,
                    "market": market.to_dict(),
                },
            )
        )
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)
        alerts.append(
            LiveReadOnlyAlert(
                severity="critical",
                code="live-read-failure",
                title="Live read failed",
                message="live exchange read stopped and reported a failure",
                details={"error": failure_reason, "symbol": request.symbol},
            )
        )
    finally:
        try:
            append_event(
                _session_event(
                    event_type=EventType.LIVE_READ_SESSION_END,
                    session_id=request.session_id,
                    suffix="end",
                    occurred_at=session_end_at,
                    payload={
                        "schema_version": LIVE_READ_SCHEMA_VERSION,
                        "operational_mode": LIVE_READ_OPERATIONAL_MODE,
                        "execution_enabled": False,
                        "source_kind": LIVE_READ_SOURCE_KIND,
                        "status": status,
                        "failure_reason": failure_reason,
                        "event_count": len(events) + 1,
                        "live_execution_proof": {
                            "order_endpoints_called": request.client.order_endpoints_called,
                            "blocked_methods": list(request.client.blocked_methods),
                        },
                    },
                )
            )
        except LiveReadOnlySessionError:
            pass

    return LiveReadOnlyReport(
        schema_version=LIVE_READ_SCHEMA_VERSION,
        session_id=request.session_id,
        journal_path=str(request.journal.path),
        operational_mode=LIVE_READ_OPERATIONAL_MODE,
        execution_enabled=False,
        source_kind=LIVE_READ_SOURCE_KIND,
        status=status,
        failure_reason=failure_reason,
        generated_at=session_end_at,
        account=account,
        symbol_metadata=symbol_metadata,
        market=market,
        normalized_market=normalized_market,
        events=tuple(events),
        alerts=tuple(alerts),
        live_execution_proof=LiveExecutionProof(
            order_endpoints_called=request.client.order_endpoints_called,
            blocked_methods=request.client.blocked_methods,
        ),
    )


def format_live_read_only_report(report: LiveReadOnlyReport, *, redact_sensitive: bool = False) -> str:
    payload = report.to_dict(redact_sensitive=redact_sensitive)
    from .secrets import assert_no_credential_leak
    output = json.dumps(payload, indent=2, sort_keys=True)
    assert_no_credential_leak(output, context="live-read report")
    return output


def format_live_read_only_report_markdown(report: LiveReadOnlyReport, *, redact_sensitive: bool = False) -> str:
    payload = report.to_dict(redact_sensitive=redact_sensitive)
    lines = [
        f"# Live read report: {payload['session_id']}",
        f"- status: {payload['status']}",
        f"- operational_mode: {payload['operational_mode']}",
        f"- execution_enabled: {payload['execution_enabled']}",
        f"- source_kind: {payload['source_kind']}",
        f"- generated_at: {payload['generated_at']}",
        f"- journal_path: {payload['journal_path']}",
    ]
    if payload.get("failure_reason"):
        lines.append(f"- failure_reason: {payload['failure_reason']}")
    if payload.get("alerts"):
        lines.append("\n## Alerts")
        for alert in payload["alerts"]:
            lines.append(f"- [{alert['severity']}] {alert['code']}: {alert['message']}")
    if payload.get("account"):
        lines.append("\n## Account")
        account = payload["account"]
        lines.append(f"- can_trade: {account['can_trade']}")
        lines.append(f"- can_withdraw: {account['can_withdraw']}")
        lines.append(f"- can_deposit: {account['can_deposit']}")
        lines.append(f"- permissions: {', '.join(account['permissions'])}")
    if payload.get("symbol_metadata"):
        metadata = payload["symbol_metadata"]
        lines.append("\n## Symbol metadata")
        lines.append(f"- symbol: {metadata['symbol']}")
        lines.append(f"- status: {metadata['status']}")
        lines.append(f"- base_asset_precision: {metadata['base_asset_precision']}")
        lines.append(f"- quote_asset_precision: {metadata['quote_asset_precision']}")
        lines.append(f"- price_precision: {metadata['price_precision']}")
        lines.append(f"- quantity_precision: {metadata['quantity_precision']}")
    if payload.get("market"):
        market = payload["market"]
        lines.append("\n## Market")
        lines.append(f"- symbol: {market['symbol']}")
        lines.append(f"- ticker_price_text: {market['ticker_price_text']}")
        lines.append(f"- observed_at: {market['observed_at']}")

    from .secrets import assert_no_credential_leak
    output = "\n".join(lines)
    assert_no_credential_leak(output, context="live-read markdown report")
    return output


def _session_event(
    *,
    event_type: EventType,
    session_id: str,
    suffix: str,
    occurred_at: datetime,
    payload: dict[str, object],
    symbol: str | None = None,
    trade_id: str | None = None,
) -> JournalEvent:
    return JournalEvent(
        event_id=f"{session_id}:{suffix}",
        event_type=event_type,
        schema_version=LIVE_READ_SCHEMA_VERSION,
        source_module="live_read_only_binance_client",
        occurred_at=occurred_at.astimezone(timezone.utc),
        correlation_id=session_id,
        symbol=symbol,
        trade_id=trade_id,
        payload=payload,
    )


def _redacted_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base_url


def _require_timezone_aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _precision_from_step(step: float) -> int:
    step_text = f"{step:.16f}".rstrip("0").rstrip(".")
    if "." not in step_text:
        return 0
    return len(step_text.split(".", 1)[1])


def _price_matches_tick(price_text: str, tick_size: float) -> bool:
    price = float(price_text)
    if tick_size <= 0:
        return True
    steps = round(price / tick_size)
    return abs((steps * tick_size) - price) <= max(1e-12, tick_size * 1e-9)


def _is_write_method(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "DELETE", "PATCH"}


def _is_write_path(path: str) -> bool:
    blocked_paths = {
        "/api/v3/order",
        "/api/v3/order/test",
        "/api/v3/batchOrders",
    }
    return path in blocked_paths
