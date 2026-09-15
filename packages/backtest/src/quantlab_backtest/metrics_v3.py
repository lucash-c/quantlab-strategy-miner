"""Exact Increment 4 metrics with explicit undefined values."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab_core.numeric import CanonicalRational

from quantlab_backtest.models_v3 import ClosedTradeV3

METRICS_ENGINE_V3_VERSION = "3.0.0"


def _defined(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "status": "DEFINED",
        "value": CanonicalRational(numerator, denominator).to_record(),
        "reason": None,
    }


def _undefined(reason: str) -> dict[str, object]:
    return {"status": "UNDEFINED", "value": None, "reason": reason}


@dataclass(slots=True)
class MetricsAccumulatorV3:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    gross_profit_units: int = 0
    gross_loss_units: int = 0
    gross_pnl_units: int = 0
    costs_units: int = 0
    slippage_impact_units: int = 0
    net_profit_units: int = 0
    net_loss_units: int = 0
    net_pnl_units: int = 0
    max_drawdown_units: int = 0
    max_consecutive_losses: int = 0
    _equity_units: int = 0
    _peak_equity_units: int = 0
    _consecutive_losses: int = 0

    def add(self, trade: ClosedTradeV3) -> None:
        self.trades += 1
        self.gross_pnl_units += trade.gross_pnl_units
        self.costs_units += trade.costs_units
        self.slippage_impact_units += trade.slippage_impact_units
        if trade.gross_pnl_units > 0:
            self.gross_profit_units += trade.gross_pnl_units
        elif trade.gross_pnl_units < 0:
            self.gross_loss_units += -trade.gross_pnl_units

        pnl = trade.net_pnl_units
        self.net_pnl_units += pnl
        self._equity_units += pnl
        self._peak_equity_units = max(self._peak_equity_units, self._equity_units)
        self.max_drawdown_units = max(
            self.max_drawdown_units, self._peak_equity_units - self._equity_units
        )
        if pnl > 0:
            self.wins += 1
            self.net_profit_units += pnl
            self._consecutive_losses = 0
        elif pnl < 0:
            self.losses += 1
            self.net_loss_units += -pnl
            self._consecutive_losses += 1
            self.max_consecutive_losses = max(
                self.max_consecutive_losses, self._consecutive_losses
            )
        else:
            self.breakeven += 1
            self._consecutive_losses = 0

    def to_record(
        self,
        *,
        price_scale: int,
        cost_model: dict[str, object],
        slippage_model: dict[str, object],
    ) -> dict[str, object]:
        average_win = (
            _defined(self.net_profit_units, self.wins)
            if self.wins
            else _undefined("NO_WINS")
        )
        average_loss = (
            _defined(-self.net_loss_units, self.losses)
            if self.losses
            else _undefined("NO_LOSSES")
        )
        payoff = (
            _defined(self.net_profit_units * self.losses, self.net_loss_units * self.wins)
            if self.wins and self.losses and self.net_loss_units
            else _undefined("NO_WINS")
            if not self.wins
            else _undefined("NO_LOSSES")
        )
        return {
            "schema_version": "backtest-metrics/v3",
            "engine_version": METRICS_ENGINE_V3_VERSION,
            "price_representation": {
                "type": "scaled_integer",
                "decimal_scale": price_scale,
            },
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "breakeven": self.breakeven,
            "gross_profit_units": self.gross_profit_units,
            "gross_loss_units": self.gross_loss_units,
            "gross_pnl_units": self.gross_pnl_units,
            "costs_units": self.costs_units,
            "slippage_impact_units": self.slippage_impact_units,
            "net_profit_units": self.net_profit_units,
            "net_loss_units": self.net_loss_units,
            "net_pnl_units": self.net_pnl_units,
            "max_drawdown_units": self.max_drawdown_units,
            "max_consecutive_losses": self.max_consecutive_losses,
            "win_rate": _defined(self.wins, self.trades)
            if self.trades
            else _undefined("NO_TRADES"),
            "average_trade": _defined(self.net_pnl_units, self.trades)
            if self.trades
            else _undefined("NO_TRADES"),
            "average_win": average_win,
            "average_loss": average_loss,
            "payoff_ratio": payoff,
            "profit_factor": _defined(self.net_profit_units, self.net_loss_units)
            if self.net_loss_units
            else _undefined("NO_LOSSES"),
            "cost_model": cost_model,
            "slippage_model": slippage_model,
            "has_open_position": False,
        }
