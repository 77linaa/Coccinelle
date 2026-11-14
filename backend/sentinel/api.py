"""Sentinel live API: streams market ticks + portfolio state to every dashboard.

Run from the repo root (so ``engine`` is importable):

    backend/.venv/bin/python -m uvicorn sentinel.api:app --app-dir backend --reload

Configuration (environment variables):

    SENTINEL_SOURCE   engine (default) | mock
    SENTINEL_SPEED    initial replay speed, x real time (default 60)
    SENTINEL_START    initial replay start, UTC (default 2026-07-30T13:00)
    SENTINEL_FORECAST_S  seconds between margin forecasts (default 2)
    SENTINEL_AI       on (default) | off — Claude-written alert explanations
                      (needs Anthropic credentials, e.g. ANTHROPIC_API_KEY;
                      without them a template explanation is used)
    SENTINEL_CORS_ORIGINS  comma-separated dashboard origins allowed to call the HTTP API
                           (default: the Vite dev server, http://localhost:5173)

HTTP
----
    GET  /health
    GET  /forecast                     latest margin forecast (or null)
    GET  /replay                       current replay state
    POST /replay/start                 play / resume (restarts if finished)
    POST /replay/pause
    POST /replay/speed?value=600       x real time, applies immediately
    POST /replay/jump?date=2026-07-30  or 2026-07-30T13:25 (UTC)

    POST /simulate        {"kind": "reduce|close|hedge", "percent": 50, "tenor": "1M"}
                          preview an action: cost, Risk Officer review, and the
                          unprotected vs protected forecast on the same random paths
    POST /protect         same body: the judge presses Protect at the current tick
    POST /protect/reset   clear the judge/Sentinel result (same timeline)
    GET  /protect         current judge vs Sentinel comparison

Every control returns the replay state:
    {"status": "idle|playing|paused|finished|error",
     "speed": 60.0, "start": "2026-07-30T13:00", "position": "2026-07-30T13:41:00Z"}

WebSocket  ws://localhost:8000/ws
---------
    {"type": "hello",  "source", "replay", "portfolio", "forecast"}      on connect
    {"type": "replay", "source", "replay": {...}}                       on any state change
    {"type": "alert",  "source",                                         level changed
     "alert": {"ts", "pair", "tenor", "prev_level", "level",             (sent before
               "score", "mid", "escalation"},                             its tick)
     "backend_ms": float}
    {"type": "tick",   "source",                                         spot prices only
     "market":    {"ts", "pair", "tenor", "mid", "spread_bps",
                   "score", "level", "latency_us"},
     "portfolio": {...} | null,     null when the tick is for an instrument not held
     "protection": {...} | null,    judge vs Sentinel, once red or Protect has happened
     "backend_ms": float}
    {"type": "protection", "source", "protection": {...}}               judge pressed / reset
    {"type": "explanation", "source", "alert": {...},                   a few seconds after
     "explanation": {"text", "source": "llm|template", "cached",        each alert
                     "as_of", "latency_ms", "model"}}

Forward quotes (1W/1M/3M) are consumed for hedging and marking but not sent as ticks.

    {"type": "forecast", "source", "as_of",                            every few seconds
     "forecast": {"breach_probability", "buffer_now", "loss_p95",        and on each alert
                  "cone": {"p5".."p95"}, "stress": {...}, ...} | null,
     "reason": str}                 only when forecast is null

Total price-to-alert latency = market.latency_us / 1000 + backend_ms.

The margin forecast never runs in the tick handler: a timer task (also woken by
level changes) snapshots the portfolio and runs the Monte Carlo in a worker
thread via ``run_in_executor``.

Slow work triggered by a level change (AI explanation, margin forecast) attaches
to ``app.state.alert_hooks``. Hooks run as background tasks, so they never delay
an alert or the next price.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sentinel.ai import Explainer
from sentinel.alerts import Alert, AlertTracker
from sentinel.feed import engine_stream, mock_stream
from sentinel.forecast import Forecaster, InsufficientHistory, ReturnHistory
from sentinel.hedges import HEDGE_TENORS, Action, QuoteBook, apply_action
from sentinel.portfolio import SPOT, Portfolio, demo_portfolio
from sentinel.race import AlreadyProtected, ProtectionRace
from sentinel.replay_control import ReplayController, SourceFactory
from sentinel.risk_officer import review

logger = logging.getLogger("sentinel.api")

SEND_TIMEOUT_S = 1.0  # a stalled browser is dropped rather than allowed to stall the feed
DEFAULT_START = "2026-07-30T13:00"
DEFAULT_SPEED = 60.0
MAX_SPEED = 1_000_000.0
DEFAULT_FORECAST_S = 2.0
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

AlertHook = Callable[[Alert], Awaitable[None]]


class ActionIn(BaseModel):
    """Body of /simulate and /protect."""

    kind: Literal["reduce", "close", "hedge"]
    percent: float = Field(100.0, gt=0, le=100, description="share to reduce / hedge ratio")
    tenor: Literal["1W", "1M", "3M"] = "1M"
    pair: str = "USDJPY"

    def to_action(self) -> Action:
        return Action(self.kind, self.pair, self.percent, self.tenor)


# ============================================================ connection manager


class ConnectionManager:
    """Tracks open dashboards and fans each message out to all of them.

    Broadcasts are serialised: ticks, alerts, forecasts and replay updates come
    from different tasks, and every dashboard must see them in the order they
    were produced, never interleaved on one socket.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._send_lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._clients)

    def add(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)

    async def join(self, websocket: WebSocket, hello: dict[str, Any]) -> None:
        """Send ``hello`` and subscribe atomically with respect to broadcasts."""
        async with self._send_lock:
            await websocket.send_json(hello)
            self._clients.add(websocket)

    def remove(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients:
            return
        data = json.dumps(message, separators=(",", ":"))  # serialise once, send N times
        async with self._send_lock:
            clients = tuple(self._clients)
            results = await asyncio.gather(
                *(asyncio.wait_for(ws.send_text(data), SEND_TIMEOUT_S) for ws in clients),
                return_exceptions=True,
            )
        for ws, result in zip(clients, results):
            if isinstance(result, BaseException):
                logger.info("Dropping client after failed send: %r", result)
                self.remove(ws)


# ============================================================ configuration


def _config_from_env() -> tuple[SourceFactory, str, float, str]:
    name = os.environ.get("SENTINEL_SOURCE", "engine").strip().lower()
    sources: dict[str, SourceFactory] = {
        # spot for the storm signal, forwards for hedging
        "engine": partial(engine_stream, tenors=(SPOT, *HEDGE_TENORS)),
        "mock": mock_stream,
    }
    if name not in sources:
        raise ValueError(f"SENTINEL_SOURCE must be one of {sorted(sources)}, got {name!r}")
    speed = float(os.environ.get("SENTINEL_SPEED", DEFAULT_SPEED))
    start = os.environ.get("SENTINEL_START", DEFAULT_START)
    return sources[name], name, speed, start


# ============================================================ app


def create_app(
    source: SourceFactory | None = None,
    *,
    source_name: str | None = None,
    portfolio_factory: Callable[[], Portfolio] = demo_portfolio,
    speed: float | None = None,
    start: str | None = None,
    autoplay: bool = True,
    alert_hooks: Iterable[AlertHook] = (),
    forecaster: Forecaster | Literal["auto"] | None = "auto",
    forecast_interval_s: float | None = None,
    explainer: Explainer | None = None,
) -> FastAPI:
    env_source, env_name, env_speed, env_start = _config_from_env()
    if source is None:
        source, source_name = env_source, source_name or env_name
    source_name = source_name or "custom"
    speed = env_speed if speed is None else speed
    start = env_start if start is None else start
    if forecast_interval_s is None:
        forecast_interval_s = float(os.environ.get("SENTINEL_FORECAST_S", DEFAULT_FORECAST_S))

    manager = ConnectionManager()
    portfolio = portfolio_factory()
    alerts = AlertTracker()
    quotes = QuoteBook()
    race = ProtectionRace()
    hooks: list[AlertHook] = list(alert_hooks)
    background: set[asyncio.Task[None]] = set()

    def run_hooks(alert: Alert) -> None:
        """Fire-and-forget: slow alert consumers never block the fast path."""
        for hook in hooks:
            task = asyncio.create_task(hook(alert), name=f"alert-hook:{hook.__name__}")
            background.add(task)
            task.add_done_callback(_finish_background(background))

    async def on_event(arrived_ns: int, event: dict[str, Any]) -> None:
        """Per price: quote book, level change, portfolios, latency stamp, broadcast."""
        try:
            quotes.update(event)
            spot = event["tenor"] == SPOT
            alert = alerts.update(event) if spot else None
            snapshot = portfolio.on_price(event)
            race.on_event(event, portfolio, quotes)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed event %r: %s", event, exc)
            return
        if not spot:
            return  # forward quote: book and marks updated, nothing to show

        if alert is not None:  # alert goes out first: it is what latency is about
            alert_msg = {"type": "alert", "source": source_name, "alert": alert.to_dict()}
            alert_msg["backend_ms"] = (time.perf_counter_ns() - arrived_ns) / 1e6
            await manager.broadcast(alert_msg)
            run_hooks(alert)

        message = {
            "type": "tick",
            "source": source_name,
            "market": event,
            "portfolio": snapshot.to_dict() if snapshot is not None else None,
            "protection": race.summary(portfolio) if race.active else None,
        }
        message["backend_ms"] = (time.perf_counter_ns() - arrived_ns) / 1e6
        await manager.broadcast(message)

    # -------------------------------------------------------- forecast (off the fast path)

    forecast: dict[str, Any] = {
        "status": "off" if forecaster is None else "loading",
        "latest": None,
        "forecaster": None,
    }
    forecast_wake = asyncio.Event()

    async def wake_forecast(_: Alert) -> None:
        forecast_wake.set()

    if forecaster is not None:
        hooks.append(wake_forecast)

    async def load_forecaster() -> Forecaster | None:
        if isinstance(forecaster, Forecaster):
            return forecaster
        pairs = {pos.pair for pos in portfolio.positions}
        try:
            history = await asyncio.get_running_loop().run_in_executor(
                None, ReturnHistory.load, pairs)
        except (ImportError, FileNotFoundError) as exc:
            logger.warning("Margin forecast unavailable: %s", exc)
            return None
        return Forecaster(history)

    async def forecast_loop() -> None:
        fc = await load_forecaster()
        forecast["status"] = "ready" if fc is not None else "unavailable"
        forecast["forecaster"] = fc
        if fc is None:
            return
        loop = asyncio.get_running_loop()
        last_as_of = None
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(forecast_wake.wait(), forecast_interval_s)
            forecast_wake.clear()
            as_of = portfolio.as_of
            if as_of is None or as_of == last_as_of:
                continue  # paused or no new price: nothing new to forecast
            snapshot = portfolio.copy()  # the worker never sees a half-updated portfolio
            message: dict[str, Any] = {"type": "forecast", "source": source_name, "as_of": as_of}
            try:
                message["forecast"] = await loop.run_in_executor(None, fc.run, snapshot, as_of)
            except InsufficientHistory as exc:
                message.update(forecast=None, reason=str(exc))
            except Exception:
                logger.exception("Margin forecast failed at %s", as_of)
                continue
            last_as_of = as_of
            forecast["latest"] = message
            await manager.broadcast(message)

    # -------------------------------------------------------- AI explanations (alert hook)

    explainer = explainer if explainer is not None else Explainer()

    async def explain_alert(alert: Alert) -> None:
        """Own task: never delays the alert or the next price."""
        spot_quote = quotes.find(alert.pair, SPOT)
        latest = forecast["latest"]
        context = {
            "spread_bps": spot_quote.spread_bps if spot_quote else None,
            "portfolio": portfolio.snapshot().to_dict() if portfolio.positions else None,
            "forecast": latest["forecast"] if latest and latest.get("forecast") else None,
        }
        explanation = await explainer.explain(alert, context)
        await manager.broadcast({"type": "explanation", "source": source_name,
                                 "alert": alert.to_dict(),
                                 "explanation": explanation.to_dict()})

    hooks.append(explain_alert)

    async def on_state(state: dict[str, Any]) -> None:
        await manager.broadcast({"type": "replay", "source": source_name, "replay": state})

    def fresh_source(start_at: str | None):
        alerts.reset()  # a restart or jump begins a new timeline
        quotes.clear()
        race.reset_timeline()
        return source(start_at)

    replay = ReplayController(fresh_source, on_event, start=start, speed=speed,
                              on_state=on_state)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if forecaster is not None:
            task = asyncio.create_task(forecast_loop(), name="forecast")
            background.add(task)
            task.add_done_callback(_finish_background(background))
        if autoplay:
            await replay.play()
        try:
            yield
        finally:
            await replay.shutdown()
            for task in tuple(background):
                task.cancel()

    app = FastAPI(title="Sentinel API", lifespan=lifespan)
    origins = os.environ.get("SENTINEL_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.manager = manager
    app.state.portfolio = portfolio
    app.state.alert_hooks = hooks
    app.state.replay = replay

    # -------------------------------------------------------- HTTP

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "source": source_name, "clients": manager.count,
                "forecast": forecast["status"],
                "ai": explainer.status}

    @app.get("/forecast")
    async def latest_forecast() -> dict[str, Any] | None:
        return forecast["latest"]

    @app.get("/replay")
    async def replay_state() -> dict[str, Any]:
        return replay.state()

    @app.post("/replay/start")
    async def replay_start() -> dict[str, Any]:
        return await replay.play()

    @app.post("/replay/pause")
    async def replay_pause() -> dict[str, Any]:
        return await replay.pause()

    @app.post("/replay/speed")
    async def replay_speed(
        value: Annotated[float, Query(gt=0, le=MAX_SPEED, description="x real time")],
    ) -> dict[str, Any]:
        return await replay.set_speed(value)

    @app.post("/replay/jump")
    async def replay_jump(
        date: Annotated[str, Query(description="UTC, e.g. 2026-07-30 or 2026-07-30T13:25")],
    ) -> dict[str, Any]:
        try:
            return await replay.jump(date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid date {date!r}: {exc}") from exc

    # -------------------------------------------------------- protection

    def _require_prices() -> None:
        if portfolio.as_of is None:
            raise HTTPException(status_code=409, detail="No prices yet: start the replay first")

    @app.post("/simulate")
    async def simulate_action(body: ActionIn) -> dict[str, Any]:
        """Preview an action without applying it."""
        _require_prices()
        before = portfolio.copy()
        book = quotes.copy()
        try:
            action = body.to_action()
            result = apply_action(before, action, book)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        spot_quote = book.find(action.pair, SPOT)
        level = spot_quote.level if spot_quote is not None else None
        verdict = review(action, before, result.portfolio, level=level, ts=before.as_of)

        fc: Forecaster | None = forecast["forecaster"]
        comparison = None
        if fc is not None:
            try:
                comparison = await asyncio.get_running_loop().run_in_executor(
                    None, fc.compare, before, result.portfolio, before.as_of)
            except InsufficientHistory:
                comparison = None
        return {
            "as_of": before.as_of,
            "review": verdict,
            "before": before.snapshot().to_dict(),
            **result.to_dict(),
            "forecast": comparison,
        }

    @app.post("/protect")
    async def protect(body: ActionIn) -> dict[str, Any]:
        """The judge presses Protect at the current tick."""
        _require_prices()
        try:
            outcome = race.protect(body.to_action(), portfolio, quotes)
        except AlreadyProtected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if outcome["approved"]:
            await manager.broadcast({"type": "protection", "source": source_name,
                                     "protection": outcome["protection"]})
        return outcome

    @app.post("/protect/reset")
    async def protect_reset() -> dict[str, Any]:
        race.reset_protection()
        summary = race.summary(portfolio)
        await manager.broadcast({"type": "protection", "source": source_name,
                                 "protection": summary})
        return summary

    @app.get("/protect")
    async def protect_state() -> dict[str, Any]:
        return race.summary(portfolio)

    # -------------------------------------------------------- WebSocket

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            # Send the current state, then join the broadcast, as one step so the
            # dashboard never renders empty and never misses or doubles a message.
            await manager.join(websocket, {
                "type": "hello",
                "source": source_name,
                "replay": replay.state(),
                "portfolio": portfolio.snapshot().to_dict(),
                "forecast": forecast["latest"],
            })
            while True:
                await websocket.receive_text()  # client messages are ignored for now
        except WebSocketDisconnect:
            pass
        finally:
            manager.remove(websocket)

    return app


def _finish_background(tasks: set[asyncio.Task[None]]) -> Callable[[asyncio.Task[None]], None]:
    def done(task: asyncio.Task[None]) -> None:
        tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background task %s failed", task.get_name(), exc_info=task.exception())

    return done


app = create_app()
