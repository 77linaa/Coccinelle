"""Per-price portfolio mark-to-market: P&L, equity, maintenance margin and buffer.

This module sits on the hot path: it runs once for every price the replay
emits, so it does O(1) work per message — no pandas, no history, no loops
over past prices. The account keeps only the latest mark for each instrument
it holds and ignores everything else.

Formulas (all reported in USD, the account currency):

    P&L          = units × (mark − entry)        quote-currency amount
                   ÷ mark  when USD is the base  (e.g. USD/JPY → JPY ÷ mark)
    Equity       = cash + Σ P&L
    Notional     = Σ over pairs |net exposure in USD|   (tenors of a pair net off)
    Maintenance  = maintenance_rate × notional
    Buffer       = equity − maintenance
    Leverage     = notional ÷ equity

Units are signed: positive is long the base currency, negative is short.
Margin is charged on the *net* position per currency pair, as FX brokers do:
a long spot position hedged with a short forward in the same pair offsets.

All portfolios built here are FICTIONAL and for demonstration only. The SGX
dataset holds market quotes, not customer accounts or margin rules.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ACCOUNT_CCY",
    "DEFAULT_MAINTENANCE_RATE",
    "SPOT",
    "Position",
    "PortfolioSnapshot",
    "Portfolio",
    "demo_portfolio",
]

ACCOUNT_CCY = "USD"
SPOT = "SPOT"
DEFAULT_MAINTENANCE_RATE = 0.03


def _normalise_pair(pair: str) -> str:
    """Return a canonical ``"USDJPY"`` key for ``"USD/JPY"``, ``"usdjpy"``, etc."""
    key = pair.replace("/", "").replace("_", "").strip().upper()
    if len(key) != 6 or not key.isalpha():
        raise ValueError(f"Unrecognised currency pair: {pair!r}")
    return key


def _normalise_tenor(tenor: str) -> str:
    return tenor.strip().upper()


def _validate_price(price: float, what: str) -> float:
    price = float(price)
    if not (math.isfinite(price) and price > 0.0):
        raise ValueError(f"{what} must be a positive finite number, got {price!r}")
    return price


@dataclass(frozen=True, slots=True)
class Position:
    """An open FX position. ``units`` is in the base currency, signed (+long / −short)."""

    pair: str
    units: float
    entry_price: float
    tenor: str = SPOT

    def __post_init__(self) -> None:
        # Frozen dataclass: canonicalise inputs via object.__setattr__.
        object.__setattr__(self, "pair", _normalise_pair(self.pair))
        object.__setattr__(self, "tenor", _normalise_tenor(self.tenor))
        object.__setattr__(self, "entry_price", _validate_price(self.entry_price, "entry_price"))
        units = float(self.units)
        if not math.isfinite(units) or units == 0.0:
            raise ValueError(f"units must be a non-zero finite number, got {self.units!r}")
        object.__setattr__(self, "units", units)
        if ACCOUNT_CCY not in (self.base, self.quote):
            raise ValueError(
                f"{self.pair}: only pairs against {ACCOUNT_CCY} are supported "
                "(the SGX dataset covers EUR/USD, USD/JPY and USD/SGD)"
            )

    @property
    def base(self) -> str:
        return self.pair[:3]

    @property
    def quote(self) -> str:
        return self.pair[3:]

    @property
    def instrument(self) -> tuple[str, str]:
        return (self.pair, self.tenor)

    def pnl_usd(self, mark: float) -> float:
        """Unrealised P&L in USD at ``mark``."""
        pnl_quote = self.units * (mark - self.entry_price)
        return pnl_quote if self.quote == ACCOUNT_CCY else pnl_quote / mark

    def exposure_usd(self, mark: Any) -> Any:
        """Signed exposure in USD at ``mark`` (+ long base currency)."""
        return self.units * (mark if self.quote == ACCOUNT_CCY else 1.0)

    def notional_usd(self, mark: Any) -> Any:
        """Absolute exposure in USD at ``mark``."""
        return abs(self.exposure_usd(mark))


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Immutable view of the account after a price update, ready to serialise."""

    as_of: Any
    cash: float
    unrealised_pnl: float
    equity: float
    notional: float
    maintenance_margin: float
    margin_buffer: float
    leverage: float | None  # None when equity <= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "currency": ACCOUNT_CCY,
            "fictional": True,
            "cash": self.cash,
            "unrealised_pnl": self.unrealised_pnl,
            "equity": self.equity,
            "notional": self.notional,
            "maintenance_margin": self.maintenance_margin,
            "margin_buffer": self.margin_buffer,
            "leverage": self.leverage,
        }


class Portfolio:
    """A fictional margin account marked to market one price message at a time.

    Before any price arrives, each position is marked at its entry price, so
    the opening snapshot shows zero P&L and the full starting buffer.
    """

    __slots__ = (
        "cash",
        "maintenance_rate",
        "positions",
        "_marks",
        "_by_instrument",
        "_pnl",
        "_exposure",
        "_as_of",
    )

    def __init__(
        self,
        cash: float,
        positions: list[Position] | tuple[Position, ...],
        maintenance_rate: float = DEFAULT_MAINTENANCE_RATE,
        *,
        marks: Mapping[tuple[str, str], float] | None = None,
        as_of: Any = None,
    ) -> None:
        """``marks`` seeds current prices per instrument (default: entry prices)."""
        if not math.isfinite(cash):
            raise ValueError(f"cash must be finite, got {cash!r}")
        if not 0.0 < maintenance_rate < 1.0:
            raise ValueError(f"maintenance_rate must be in (0, 1), got {maintenance_rate!r}")

        self.cash = float(cash)
        self.maintenance_rate = float(maintenance_rate)
        self.positions: tuple[Position, ...] = tuple(positions)

        # Index positions by instrument once, so each message is a dict lookup.
        by_instrument: dict[tuple[str, str], list[int]] = {}
        for i, pos in enumerate(self.positions):
            by_instrument.setdefault(pos.instrument, []).append(i)
        self._by_instrument = {k: tuple(v) for k, v in by_instrument.items()}

        # Latest mark per held instrument (seeded at entry unless given);
        # per-position caches of P&L and signed exposure.
        marks = marks or {}
        self._marks: dict[tuple[str, str], float] = {
            pos.instrument: _validate_price(marks.get(pos.instrument, pos.entry_price), "mark")
            for pos in self.positions
        }
        self._pnl = [pos.pnl_usd(self._marks[pos.instrument]) for pos in self.positions]
        self._exposure = [pos.exposure_usd(self._marks[pos.instrument]) for pos in self.positions]
        self._as_of: Any = as_of

    # ------------------------------------------------------------------ hot path

    def on_price(self, message: Mapping[str, Any]) -> PortfolioSnapshot | None:
        """Apply one replay message and return the new snapshot.

        Expects the engine stream keys ``pair``, ``tenor``, ``mid`` and
        ``ts``. Returns ``None`` (and changes nothing) when the message is for
        an instrument this portfolio does not hold, so callers can skip pushing
        an unchanged portfolio to the dashboard.
        """
        instrument = (
            _normalise_pair(message["pair"]),
            _normalise_tenor(message.get("tenor") or SPOT),
        )
        indices = self._by_instrument.get(instrument)
        if indices is None:
            return None

        mark = _validate_price(message["mid"], "mid")
        self._marks[instrument] = mark
        for i in indices:
            pos = self.positions[i]
            self._pnl[i] = pos.pnl_usd(mark)
            self._exposure[i] = pos.exposure_usd(mark)
        self._as_of = message.get("ts")
        return self.snapshot()

    def snapshot(self) -> PortfolioSnapshot:
        pnl = math.fsum(self._pnl)
        notional = math.fsum(abs(e) for e in self._net_by_pair(self._exposure).values())
        equity = self.cash + pnl
        maintenance = self.maintenance_rate * notional
        return PortfolioSnapshot(
            as_of=self._as_of,
            cash=self.cash,
            unrealised_pnl=pnl,
            equity=equity,
            notional=notional,
            maintenance_margin=maintenance,
            margin_buffer=equity - maintenance,
            leverage=notional / equity if equity > 0 else None,
        )

    def _net_by_pair(self, exposures: list[Any]) -> dict[str, Any]:
        net: dict[str, Any] = {}
        for pos, exposure in zip(self.positions, exposures):
            net[pos.pair] = net.get(pos.pair, 0.0) + exposure
        return net

    # ------------------------------------------------------------------ what-if

    def revalue(self, marks: Mapping[tuple[str, str], Any]) -> tuple[Any, Any]:
        """Equity and maintenance margin at hypothetical marks. Pure: no state change.

        ``marks`` maps ``(pair, tenor)`` to a price or a NumPy array of prices
        (e.g. simulated paths); instruments not given keep their current mark.
        Uses the same formulas as the live path, so scenarios cannot drift from it.
        """
        pnl: Any = 0.0
        exposures = []
        for pos in self.positions:
            mark = marks.get(pos.instrument, self._marks[pos.instrument])
            pnl = pnl + pos.pnl_usd(mark)
            exposures.append(pos.exposure_usd(mark))
        notional: Any = 0.0
        for net in self._net_by_pair(exposures).values():
            notional = notional + abs(net)
        return self.cash + pnl, self.maintenance_rate * notional

    def copy(self) -> Portfolio:
        """Independent snapshot, safe to hand to a worker thread."""
        clone = Portfolio.__new__(Portfolio)
        clone.cash = self.cash
        clone.maintenance_rate = self.maintenance_rate
        clone.positions = self.positions          # tuple of frozen Positions
        clone._by_instrument = self._by_instrument  # never mutated after __init__
        clone._marks = dict(self._marks)
        clone._pnl = list(self._pnl)
        clone._exposure = list(self._exposure)
        clone._as_of = self._as_of
        return clone

    # ------------------------------------------------------------------ helpers

    @property
    def as_of(self) -> Any:
        """Timestamp of the last price applied (``None`` before the first)."""
        return self._as_of

    def net_exposures(self) -> dict[str, float]:
        """Net signed USD exposure per pair at current marks."""
        return self._net_by_pair(self._exposure)

    def marks(self) -> dict[tuple[str, str], float]:
        """Copy of the latest mark per held instrument."""
        return dict(self._marks)

    def mark(self, pair: str, tenor: str = SPOT) -> float:
        """Latest mark for a held instrument."""
        return self._marks[(_normalise_pair(pair), _normalise_tenor(tenor))]

    def __repr__(self) -> str:
        return (
            f"Portfolio(cash={self.cash:,.2f}, positions={len(self.positions)}, "
            f"maintenance_rate={self.maintenance_rate:.2%})"
        )


def demo_portfolio() -> Portfolio:
    """FICTIONAL demo account: long 100,000 USD/JPY spot at 155.00 with 6,000 USD cash."""
    return Portfolio(
        cash=6_000.0,
        positions=[Position(pair="USD/JPY", units=100_000.0, entry_price=155.00)],
    )
