from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterable

from .journal import AppendOnlyJournal, EventType
from .paper import PAPER_TRADING_DEFAULT_QUOTE_ASSET

PROMETHEUS_SCHEMA_VERSION = "1.0.0"
PAPER_TRADING_PROMETHEUS_SCHEMA_VERSION = PROMETHEUS_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AllocationValue:
    asset: str
    symbol: str
    value_quote: float


@dataclass(frozen=True, slots=True)
class TransactionDatum:
    occurred_at: datetime
    asset: str
    symbol: str
    side: str
    quantity: float
    execution_price_quote: float
    notional_quote: float
    fee_quote: float
    fill_status: str
    trade_id: str
    order_id: str
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class CountDatum:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class PaperTradingPrometheusSnapshot:
    schema_version: str
    generated_at: datetime
    journal_path: str
    session_id: str
    quote_asset: str
    status: str
    event_count: int
    cash_quote: float
    equity_quote: float
    realized_pnl_quote: float
    unrealized_pnl_quote: float
    total_pnl_quote: float
    drawdown_quote: float
    drawdown_pct: float
    allocations: tuple[AllocationValue, ...]
    recent_transactions: tuple[TransactionDatum, ...]
    operation_counts: tuple[CountDatum, ...]
    journal_event_counts: tuple[CountDatum, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "journal_path": self.journal_path,
            "session_id": self.session_id,
            "quote_asset": self.quote_asset,
            "status": self.status,
            "event_count": self.event_count,
            "cash_quote": self.cash_quote,
            "equity_quote": self.equity_quote,
            "realized_pnl_quote": self.realized_pnl_quote,
            "unrealized_pnl_quote": self.unrealized_pnl_quote,
            "total_pnl_quote": self.total_pnl_quote,
            "drawdown_quote": self.drawdown_quote,
            "drawdown_pct": self.drawdown_pct,
            "allocations": [
                {
                    "asset": item.asset,
                    "symbol": item.symbol,
                    "value_quote": item.value_quote,
                }
                for item in self.allocations
            ],
            "recent_transactions": [
                {
                    "occurred_at": item.occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "asset": item.asset,
                    "symbol": item.symbol,
                    "side": item.side,
                    "quantity": item.quantity,
                    "execution_price_quote": item.execution_price_quote,
                    "notional_quote": item.notional_quote,
                    "fee_quote": item.fee_quote,
                    "fill_status": item.fill_status,
                    "trade_id": item.trade_id,
                    "order_id": item.order_id,
                    "decision_id": item.decision_id,
                }
                for item in self.recent_transactions
            ],
            "operation_counts": [
                {"label": item.label, "count": item.count} for item in self.operation_counts
            ],
            "journal_event_counts": [
                {"label": item.label, "count": item.count} for item in self.journal_event_counts
            ],
        }


def build_paper_trading_prometheus_snapshot(
    journal: AppendOnlyJournal | str | Path,
    *,
    session_id: str | None = None,
) -> PaperTradingPrometheusSnapshot:
    journal_obj = journal if isinstance(journal, AppendOnlyJournal) else AppendOnlyJournal(journal)
    end_events = journal_obj.query(event_types=(EventType.PAPER_TRADING_SESSION_END,), correlation_id=session_id)
    if not end_events:
        if session_id is None:
            raise ValueError("no paper trading session end events found in journal")
        raise ValueError(f"session not found: {session_id}")

    end_event = end_events[-1]
    payload = end_event.payload or {}
    final_portfolio = _as_mapping(payload.get("final_portfolio"), name="final_portfolio")
    pnl_snapshot = _as_mapping(payload.get("pnl_snapshot"), name="pnl_snapshot")
    quote_asset = str(payload.get("quote_asset", PAPER_TRADING_DEFAULT_QUOTE_ASSET))
    session_id = end_event.correlation_id

    events = journal_obj.query(correlation_id=session_id)
    event_type_counts = Counter(event.event_type.value for event in events)

    cash_quote = float(final_portfolio.get("cash_quote", 0.0))
    equity_quote = float(final_portfolio.get("equity_quote", cash_quote))
    realized_pnl_quote = float(pnl_snapshot.get("realized_pnl_quote", final_portfolio.get("realized_pnl_quote", 0.0)))
    unrealized_pnl_quote = float(pnl_snapshot.get("unrealized_pnl_quote", final_portfolio.get("unrealized_pnl_quote", 0.0)))
    total_pnl_quote = float(pnl_snapshot.get("total_pnl_quote", equity_quote - float(pnl_snapshot.get("initial_cash_quote", cash_quote))))
    drawdown_quote = float(pnl_snapshot.get("drawdown_quote", max(float(pnl_snapshot.get("initial_cash_quote", cash_quote)) - equity_quote, 0.0)))
    drawdown_pct = float(pnl_snapshot.get("drawdown_pct", 0.0))
    status = str(payload.get("status", "unknown"))

    allocations = [
        AllocationValue(asset=quote_asset, symbol=quote_asset, value_quote=abs(cash_quote)),
    ]
    for position in _iter_positions(final_portfolio.get("positions", ()), quote_asset=quote_asset):
        allocations.append(position)

    fill_events = journal_obj.query(event_types=(EventType.PAPER_ORDER_FILL,))
    recent_transactions = tuple(
        TransactionDatum(
            occurred_at=event.occurred_at,
            asset=str((event.payload or {}).get("asset") or _symbol_to_asset(str((event.payload or {}).get("symbol", event.symbol or "")), quote_asset=quote_asset)),
            symbol=str((event.payload or {}).get("symbol", event.symbol or "")),
            side=str((event.payload or {}).get("side", "")),
            quantity=float((event.payload or {}).get("filled_quantity", 0.0)),
            execution_price_quote=float((event.payload or {}).get("execution_price_quote", 0.0)),
            notional_quote=float((event.payload or {}).get("notional_quote", 0.0)),
            fee_quote=float((event.payload or {}).get("fee_quote", 0.0)),
            fill_status=str((event.payload or {}).get("fill_status", "")),
            trade_id=str((event.payload or {}).get("trade_id", event.trade_id or "")),
            order_id=str((event.payload or {}).get("order_id", "")),
            decision_id=None if (event.payload or {}).get("decision_id") is None else str((event.payload or {}).get("decision_id")),
        )
        for event in reversed(fill_events[-10:])
    )

    operation_counts = tuple(
        CountDatum(label=label, count=count)
        for label, count in (
            ("strategy_proposal", int(payload.get("strategy_proposal_count", event_type_counts.get(EventType.STRATEGY_PROPOSAL.value, 0)))),
            ("approved_decision", int(payload.get("approved_decision_count", 0))),
            ("rejected_decision", int(payload.get("rejected_decision_count", 0))),
            ("paper_order", int(payload.get("paper_order_count", event_type_counts.get(EventType.PAPER_ORDER_CREATED.value, 0)))),
            ("fill", int(payload.get("fill_count", event_type_counts.get(EventType.PAPER_ORDER_FILL.value, 0)))),
            ("portfolio_snapshot", event_type_counts.get(EventType.PAPER_PORTFOLIO_SNAPSHOT.value, 0)),
            ("pnl_snapshot", event_type_counts.get(EventType.PAPER_PNL_SNAPSHOT.value, 0)),
            ("session_start", event_type_counts.get(EventType.PAPER_TRADING_SESSION_START.value, 0)),
            ("session_end", event_type_counts.get(EventType.PAPER_TRADING_SESSION_END.value, 0)),
        )
    )
    journal_event_counts = tuple(
        CountDatum(label=label, count=count)
        for label, count in sorted(event_type_counts.items())
    )

    return PaperTradingPrometheusSnapshot(
        schema_version=PROMETHEUS_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc),
        journal_path=str(journal_obj.path),
        session_id=session_id,
        quote_asset=quote_asset,
        status=status,
        event_count=len(events),
        cash_quote=cash_quote,
        equity_quote=equity_quote,
        realized_pnl_quote=realized_pnl_quote,
        unrealized_pnl_quote=unrealized_pnl_quote,
        total_pnl_quote=total_pnl_quote,
        drawdown_quote=drawdown_quote,
        drawdown_pct=drawdown_pct,
        allocations=tuple(allocations),
        recent_transactions=recent_transactions,
        operation_counts=operation_counts,
        journal_event_counts=journal_event_counts,
    )


def render_paper_trading_prometheus_text(snapshot: PaperTradingPrometheusSnapshot) -> str:
    lines: list[str] = []
    lines.append("# HELP trading_autopilot_session_info Paper trading session metadata snapshot.")
    lines.append("# TYPE trading_autopilot_session_info gauge")
    lines.append(
        _metric_line(
            "trading_autopilot_session_info",
            1,
            labels={
                "session_id": snapshot.session_id,
                "journal_path": snapshot.journal_path,
                "quote_asset": snapshot.quote_asset,
                "status": snapshot.status,
            },
        )
    )

    lines.append("# HELP trading_autopilot_balance_quote Balance values in quote currency.")
    lines.append("# TYPE trading_autopilot_balance_quote gauge")
    lines.extend(
        [
            _metric_line("trading_autopilot_balance_quote", snapshot.cash_quote, labels={"session_id": snapshot.session_id, "kind": "cash"}),
            _metric_line("trading_autopilot_balance_quote", snapshot.equity_quote, labels={"session_id": snapshot.session_id, "kind": "equity"}),
        ]
    )

    lines.append("# HELP trading_autopilot_pnl_quote Profit-and-loss values in quote currency.")
    lines.append("# TYPE trading_autopilot_pnl_quote gauge")
    lines.extend(
        [
            _metric_line("trading_autopilot_pnl_quote", snapshot.realized_pnl_quote, labels={"session_id": snapshot.session_id, "kind": "realized"}),
            _metric_line("trading_autopilot_pnl_quote", snapshot.unrealized_pnl_quote, labels={"session_id": snapshot.session_id, "kind": "unrealized"}),
            _metric_line("trading_autopilot_pnl_quote", snapshot.total_pnl_quote, labels={"session_id": snapshot.session_id, "kind": "total"}),
            _metric_line("trading_autopilot_pnl_quote", snapshot.drawdown_quote, labels={"session_id": snapshot.session_id, "kind": "drawdown"}),
            _metric_line("trading_autopilot_pnl_quote", snapshot.drawdown_pct, labels={"session_id": snapshot.session_id, "kind": "drawdown_pct"}),
        ]
    )

    lines.append("# HELP trading_autopilot_allocation_value_quote Absolute portfolio value by asset.")
    lines.append("# TYPE trading_autopilot_allocation_value_quote gauge")
    for allocation in snapshot.allocations:
        lines.append(
            _metric_line(
                "trading_autopilot_allocation_value_quote",
                allocation.value_quote,
                labels={
                    "session_id": snapshot.session_id,
                    "asset": allocation.asset,
                    "symbol": allocation.symbol,
                },
            )
        )

    lines.append("# HELP trading_autopilot_transaction_timestamp_seconds Recent fill transactions ordered by recency.")
    lines.append("# TYPE trading_autopilot_transaction_timestamp_seconds gauge")
    for item in snapshot.recent_transactions:
        lines.append(
            _metric_line(
                "trading_autopilot_transaction_timestamp_seconds",
                item.occurred_at.timestamp(),
                labels={
                    "session_id": snapshot.session_id,
                    "occurred_at": item.occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "asset": item.asset,
                    "symbol": item.symbol,
                    "side": item.side,
                    "quantity": item.quantity,
                    "execution_price_quote": item.execution_price_quote,
                    "notional_quote": item.notional_quote,
                    "fee_quote": item.fee_quote,
                    "fill_status": item.fill_status,
                    "trade_id": item.trade_id,
                    "order_id": item.order_id,
                    "decision_id": item.decision_id or "",
                },
            )
        )

    lines.append("# HELP trading_autopilot_transaction_timestamp_milliseconds Recent fill transactions ordered by recency for Grafana display.")
    lines.append("# TYPE trading_autopilot_transaction_timestamp_milliseconds gauge")
    for item in snapshot.recent_transactions:
        lines.append(
            _metric_line(
                "trading_autopilot_transaction_timestamp_milliseconds",
                item.occurred_at.timestamp() * 1000.0,
                labels={
                    "session_id": snapshot.session_id,
                    "occurred_at": item.occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "asset": item.asset,
                    "symbol": item.symbol,
                    "side": item.side,
                    "quantity": item.quantity,
                    "execution_price_quote": item.execution_price_quote,
                    "notional_quote": item.notional_quote,
                    "fee_quote": item.fee_quote,
                    "fill_status": item.fill_status,
                    "trade_id": item.trade_id,
                    "order_id": item.order_id,
                    "decision_id": item.decision_id or "",
                },
            )
        )

    lines.append("# TYPE trading_autopilot_operation_count gauge")
    for item in snapshot.operation_counts:
        lines.append(
            _metric_line(
                "trading_autopilot_operation_count",
                item.count,
                labels={"session_id": snapshot.session_id, "operation": item.label},
            )
        )

    lines.append("# HELP trading_autopilot_journal_event_count Count of journal events by event type.")
    lines.append("# TYPE trading_autopilot_journal_event_count gauge")
    for item in snapshot.journal_event_counts:
        lines.append(
            _metric_line(
                "trading_autopilot_journal_event_count",
                item.count,
                labels={"session_id": snapshot.session_id, "event_type": item.label},
            )
        )

    lines.append("# HELP trading_autopilot_snapshot_generated_at Unix timestamp when this snapshot was generated.")
    lines.append("# TYPE trading_autopilot_snapshot_generated_at gauge")
    lines.append(
        _metric_line(
            "trading_autopilot_snapshot_generated_at",
            snapshot.generated_at.timestamp(),
            labels={"session_id": snapshot.session_id},
        )
    )
    return "\n".join(lines) + "\n"


def write_paper_trading_prometheus_text(
    journal: AppendOnlyJournal | str | Path,
    *,
    session_id: str | None = None,
    output_path: str | Path,
) -> Path:
    snapshot = build_paper_trading_prometheus_snapshot(journal, session_id=session_id)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_paper_trading_prometheus_text(snapshot), encoding="utf-8")
    return path


class _MetricsHandler(BaseHTTPRequestHandler):
    snapshot_builder = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib interface
        if self.path not in ("/", "/metrics", "/healthz"):
            self.send_error(404, "not found")
            return
        if self.path == "/healthz":
            self._write_text("ok\n")
            return
        assert self.snapshot_builder is not None
        snapshot = self.snapshot_builder()
        self._write_text(render_paper_trading_prometheus_text(snapshot), content_type="text/plain; version=0.0.4; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - stdlib interface
        return

    def _write_text(self, content: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve_paper_trading_prometheus(
    journal: AppendOnlyJournal | str | Path,
    *,
    session_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 9898,
) -> None:
    journal_obj = journal if isinstance(journal, AppendOnlyJournal) else AppendOnlyJournal(journal)

    def _build_snapshot() -> PaperTradingPrometheusSnapshot:
        return build_paper_trading_prometheus_snapshot(journal_obj, session_id=session_id)

    handler = type(
        "PaperTradingMetricsHandler",
        (_MetricsHandler,),
        {"snapshot_builder": staticmethod(_build_snapshot)},
    )
    httpd = HTTPServer((host, port), handler)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export trading autopilot paper trading metrics for Prometheus.")
    parser.add_argument("--journal-path", required=True, help="Path to the paper trading journal SQLite database.")
    parser.add_argument("--session-id", help="Specific paper trading session to export. Defaults to the latest session.")
    parser.add_argument("--output", help="Write the Prometheus exposition to this file instead of stdout.")
    parser.add_argument("--serve", action="store_true", help="Serve the metrics over HTTP instead of printing once.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind when --serve is used.")
    parser.add_argument("--port", type=int, default=9898, help="Port to bind when --serve is used.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.serve:
        serve_paper_trading_prometheus(args.journal_path, session_id=args.session_id, host=args.host, port=args.port)
        return

    snapshot = build_paper_trading_prometheus_snapshot(args.journal_path, session_id=args.session_id)
    text = render_paper_trading_prometheus_text(snapshot)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _iter_positions(positions: object, *, quote_asset: str) -> tuple[AllocationValue, ...]:
    if not isinstance(positions, list):
        return ()
    items: list[AllocationValue] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol", ""))
        quantity = float(position.get("quantity", 0.0))
        last_mark_quote = float(position.get("last_mark_quote", 0.0))
        asset = _symbol_to_asset(symbol, quote_asset=quote_asset)
        items.append(AllocationValue(asset=asset, symbol=symbol, value_quote=abs(quantity * last_mark_quote)))
    return tuple(items)


def _symbol_to_asset(symbol: str, *, quote_asset: str) -> str:
    if symbol.endswith(quote_asset) and len(symbol) > len(quote_asset):
        return symbol[: -len(quote_asset)]
    return symbol


def _metric_line(name: str, value: float | int, *, labels: dict[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        parts = [f'{key}="{_escape_label_value(str(val))}"' for key, val in sorted(labels.items())]
        label_text = "{" + ",".join(parts) + "}"
    return f"{name}{label_text} {_format_number(value)}"


def _format_number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if value != value:  # NaN guard
        return "NaN"
    if value == float("inf"):
        return "+Inf"
    if value == float("-inf"):
        return "-Inf"
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r'\"')


def _as_mapping(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


if __name__ == "__main__":
    main()
