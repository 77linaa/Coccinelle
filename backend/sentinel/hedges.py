"""Protective actions, filled at the real quote of the moment.

Actions
-------
- ``reduce``: close ``percent`` of every position in the pair.
- ``close``:  reduce by 100%.
- ``hedge``:  open an opposite *simulated OTC forward* covering ``percent`` of
              the pair's net exposure, at that tenor's real forward quote.
              Labelled OTC: the dataset has FX forward quotes, not SGX-listed
              futures, so this is not an executable SGX hedge.

Fills use the engine's live quote: selling (closing a long, opening a short
hedge) fills at the bid, buying at the ask. In the SGX data ``mid`` is exactly
``(bid + ask) / 2``, so bid and ask are rebuilt exactly from ``mid`` and
``spread_bps``.

Each action returns a *new* portfolio (the input is never modified) and its
cost: the drop in equity when both portfolios are valued at current mids —
i.e. the spread paid, honestly measured, including any conversion effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sentinel.portfolio import SPOT, Portfolio, Position

__all__ = [
    "HEDGE_TENORS",
    "Action",
    "ActionResult",
    "Quote",
    "QuoteBook",
    "apply_action",
]

HEDGE_TENORS = ("1W", "1M", "3M")
OTC_LABEL = "Simulated OTC FX forward at the SGX dataset's forward quote — not an SGX-listed future"

Kind = Literal["reduce", "close", "hedge"]


# ============================================================ quotes


@dataclass(frozen=True, slots=True)
class Quote:
    ts: str
    pair: str
    tenor: str
    mid: float
    spread_bps: float
    level: str | None = None

    @property
    def half_spread(self) -> float:
        return self.mid * self.spread_bps / 2e4

    @property
    def bid(self) -> float:
        return self.mid - self.half_spread

    @property
    def ask(self) -> float:
        return self.mid + self.half_spread

    def fill(self, units: float) -> float:
        """Price for trading ``units`` (+ buy at ask, − sell at bid)."""
        return self.ask if units > 0 else self.bid


class QuoteBook:
    """Latest quote per (pair, tenor). O(1) update on the fast path."""

    __slots__ = ("_quotes",)

    def __init__(self, quotes: Mapping[tuple[str, str], Quote] | None = None) -> None:
        self._quotes: dict[tuple[str, str], Quote] = dict(quotes or {})

    def update(self, event: Mapping[str, Any]) -> Quote:
        quote = Quote(
            ts=event["ts"],
            pair=event["pair"],
            tenor=event["tenor"],
            mid=float(event["mid"]),
            spread_bps=float(event["spread_bps"]),
            level=event.get("level"),
        )
        self._quotes[(quote.pair, quote.tenor)] = quote
        return quote

    def get(self, pair: str, tenor: str = SPOT) -> Quote:
        try:
            return self._quotes[(pair, tenor)]
        except KeyError:
            raise ValueError(f"No live quote for {pair} {tenor} yet") from None

    def find(self, pair: str, tenor: str = SPOT) -> Quote | None:
        return self._quotes.get((pair, tenor))

    def copy(self) -> QuoteBook:
        return QuoteBook(self._quotes)

    def clear(self) -> None:
        self._quotes.clear()


# ============================================================ actions


@dataclass(frozen=True, slots=True)
class Action:
    kind: Kind
    pair: str = "USDJPY"
    percent: float = 100.0      # share to reduce, or hedge ratio
    tenor: str = "1M"           # hedge tenor (ignored for reduce/close)

    def __post_init__(self) -> None:
        if self.kind not in ("reduce", "close", "hedge"):
            raise ValueError(f"Unknown action {self.kind!r}")
        if self.kind == "close":
            object.__setattr__(self, "percent", 100.0)
        if not 0.0 < self.percent <= 100.0:
            raise ValueError(f"percent must be in (0, 100], got {self.percent!r}")
        if self.kind == "hedge" and self.tenor not in HEDGE_TENORS:
            raise ValueError(f"Hedge tenor must be one of {HEDGE_TENORS}, got {self.tenor!r}")

    @property
    def sells_into_market(self) -> bool:
        """True for actions that close existing positions (forced selling/buying)."""
        return self.kind in ("reduce", "close")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionResult:
    action: Action
    portfolio: Portfolio
    cost: float                       # USD, equity given up to the spread
    trades: list[dict[str, Any]] = field(default_factory=list)
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.to_dict(), "cost": self.cost,
                "trades": self.trades, "label": self.label,
                "portfolio": self.portfolio.snapshot().to_dict()}


def apply_action(portfolio: Portfolio, action: Action, quotes: QuoteBook) -> ActionResult:
    """Return the portfolio after ``action``, filled at the current quotes."""
    if action.kind == "hedge":
        cash, positions, trades = _hedge(portfolio, action, quotes)
        label = OTC_LABEL
    else:
        cash, positions, trades = _reduce(portfolio, action, quotes)
        label = None

    marks = portfolio.marks()
    for t in trades:  # new instruments are marked at their live mid
        marks.setdefault((t["pair"], t["tenor"]), quotes.get(t["pair"], t["tenor"]).mid)
    after = Portfolio(cash, positions, portfolio.maintenance_rate,
                      marks=marks, as_of=portfolio.as_of)

    equity_before, _ = portfolio.revalue({})
    equity_after, _ = after.revalue({})
    return ActionResult(action, after, cost=equity_before - equity_after,
                        trades=trades, label=label)


def _reduce(portfolio: Portfolio, action: Action, quotes: QuoteBook):
    share = action.percent / 100.0
    held = [p for p in portfolio.positions if p.pair == action.pair]
    if not held:
        raise ValueError(f"No open {action.pair} position to {action.kind}")

    cash = portfolio.cash
    positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    for pos in portfolio.positions:
        if pos.pair != action.pair:
            positions.append(pos)
            continue
        quote = quotes.get(pos.pair, pos.tenor)
        closing = -pos.units * share                     # trade that offsets the slice
        price = quote.fill(closing)
        cash += Position(pos.pair, -closing, pos.entry_price, pos.tenor).pnl_usd(price)
        remaining = pos.units + closing
        if share < 1.0:
            positions.append(Position(pos.pair, remaining, pos.entry_price, pos.tenor))
        trades.append(_trade(quote, closing, price))
    return cash, positions, trades


def _hedge(portfolio: Portfolio, action: Action, quotes: QuoteBook):
    net_units = sum(p.units for p in portfolio.positions if p.pair == action.pair)
    if net_units == 0:
        raise ValueError(f"No net {action.pair} exposure to hedge")
    quote = quotes.get(action.pair, action.tenor)
    units = -net_units * action.percent / 100.0
    price = quote.fill(units)
    positions = [*portfolio.positions, Position(action.pair, units, price, action.tenor)]
    return portfolio.cash, positions, [_trade(quote, units, price)]


def _trade(quote: Quote, units: float, price: float) -> dict[str, Any]:
    return {"ts": quote.ts, "pair": quote.pair, "tenor": quote.tenor,
            "side": "buy" if units > 0 else "sell", "units": abs(units),
            "price": price, "mid": quote.mid, "spread_bps": quote.spread_bps}
