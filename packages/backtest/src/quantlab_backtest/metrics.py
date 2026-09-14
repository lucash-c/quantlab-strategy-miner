"""Exact incremental metrics with no floating-point output."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab_backtest.models import ClosedTrade

METRICS_ENGINE_VERSION = "1.0.0"


def _ratio(numerator: int, denominator: int) -> dict[str, object] | None:
    if denominator == 0:
        return None
    return {"kind": "RATIO", "numerator": numerator, "denominator": denominator}


@dataclass(slots=True)
class MetricsAccumulator:
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    gross_profit_units: int = 0
    gross_loss_units: int = 0
    net_pnl_units: int = 0
    max_drawdown_units: int = 0
    max_consecutive_losses: int = 0
    _equity_units: int = 0
    _peak_equity_units: int = 0
    _consecutive_losses: int = 0

    def add(self, trade: ClosedTrade) -> None:
        self.closed_trades += 1
        pnl = trade.pnl_units
        self.net_pnl_units += pnl
        self._equity_units += pnl
        self._peak_equity_units = max(self._peak_equity_units, self._equity_units)
        self.max_drawdown_units = max(
            self.max_drawdown_units,
            self._peak_equity_units - self._equity_units,
        )
        if pnl > 0:
            self.wins += 1
            self.gross_profit_units += pnl
            self._consecutive_losses = 0
        elif pnl < 0:
            self.losses += 1
            self.gross_loss_units += -pnl
            self._consecutive_losses += 1
            self.max_consecutive_losses = max(
                self.max_consecutive_losses,
                self._consecutive_losses,
            )
        else:
            self.breakeven += 1
            self._consecutive_losses = 0

    def to_record(self, *, price_scale: int, open_position: bool) -> dict[str, object]:
        if self.gross_loss_units:
            profit_factor: dict[str, object] = {
                "kind": "RATIO",
                "numerator": self.gross_profit_units,
                "denominator": self.gross_loss_units,
            }
        elif self.gross_profit_units:
            profit_factor = {"kind": "INFINITE"}
        else:
            profit_factor = {"kind": "UNDEFINED"}
        return {
            "schema_version": "backtest-metrics/v1",
            "engine_version": METRICS_ENGINE_VERSION,
            "price_representation": {
                "type": "scaled_integer",
                "decimal_scale": price_scale,
            },
            "closed_trades": self.closed_trades,
            "wins": self.wins,
            "losses": self.losses,
            "breakeven": self.breakeven,
            "gross_profit_units": self.gross_profit_units,
            "gross_loss_units": self.gross_loss_units,
            "net_pnl_units": self.net_pnl_units,
            "max_drawdown_units": self.max_drawdown_units,
            "max_consecutive_losses": self.max_consecutive_losses,
            "win_rate": _ratio(self.wins, self.closed_trades),
            "average_trade_units": _ratio(self.net_pnl_units, self.closed_trades),
            "profit_factor": profit_factor,
            "has_open_position": open_position,
            "cost_model": "NONE",
            "slippage_model": "NONE",
        }
