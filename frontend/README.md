# Coccinelle — Frontend (Person C / Rayane)

React + Vite + Tailwind CSS + Recharts. This is a **standalone frontend** built while Meryem's risk engine and Helina's FastAPI backend are in progress.

## Start it

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

The app tries `ws://localhost:8000/ws` for ~1.8 seconds. If that endpoint doesn't deliver valid spot ticks, it switches to an **interactive local demo**. Replay controls, market chart, warning cards, margin graph and protection simulator work in demo mode.

The three selectable spot markets (USD/JPY, EUR/USD, USD/SGD) each have **91 real SGX challenge quotes from 30 July 2026, sampled every two minutes between 12:00 and 15:00 UTC**. All three markets use the same replay clock; switching pairs changes the chart and pair-specific score without resetting playback. The sample portfolio contains fictional positions in all three pairs. Its margin rules, scores, forecast probabilities and timing values in demo mode are **illustrative**. No orders are placed.

## Backend integration handoff

**Please agree this message shape with Helina/Meryem before changing backend/frontend independently.** `src/lib/protocol.js` accepts some snake_case aliases, but the canonical message is:

```json
{
  "type": "tick",
  "timestamp": "2026-07-30T13:30:00Z",
  "pair": "USDJPY",
  "product": "SPOT",
  "tenor": "SPOT",
  "quote": {"mid": 162.779, "bid": 162.777, "ask": 162.781},
  "storm": {"score": 67, "level": "HIGH", "drivers": ["Volatility above baseline", "Adverse position move"]},
  "portfolio": {
    "equity": 120000, "maintenanceMargin": 105000, "buffer": 15000,
    "pnl": -20000, "notional": 1000000, "leverage": 7.14,
    "exposure": [{"pair": "USD/JPY", "side": "Long USD", "notional": 1000000, "share": 1}]
  },
  "forecast": {
    "breachProbability": 0.22, "horizonHours": 24,
    "cone": [{"hour":"0h","lower":115000,"median":120000,"upper":125000}]
  },
  "latency": {"latestMs": 13, "medianMs": 14, "p99Ms": 27},
  "tideTable": [{"hour": "13:00", "risk": "HIGH"}]
}
```

The frontend accepts **USD/JPY, EUR/USD and USD/SGD spot** frames. Each pair has a separate live quote buffer, so a USD/JPY quote will never be plotted on a EUR/USD chart. FX forward messages are ignored until a tenor-specific interface is implemented. If the backend is live for only one pair, selecting a pair without live ticks transparently shows its historical replay instead of inventing live values.

### WebSocket controls sent by frontend

```json
{"type":"play"}
{"type":"pause"}
{"type":"reset"}
{"type":"speed","multiplier":5}
{"type":"jump","timestamp":"2026-07-30T13:24:00Z"}
{"type":"protect","action":"reduce_50","timestamp":"2026-07-30T13:30:00Z","pair":"USDJPY"}
```

Protect actions are `none`, `reduce_50`, and `close_all`. The backend remains the final authority for pricing, limits, execution and post-action comparisons. Optionally return:

```json
{"type":"protection_result","payload":{"approved":true,"message":"Paper reduction accepted"}}
```

The WebSocket URL can be overridden in `.env` by setting `VITE_COCCINELLE_WS_URL`. Restart Vite after changing env values.

## UI scope already done

- Dashboard navigation and responsive dark terminal design.
- Scroll-aware sidebar navigation that highlights the visible workspace section.
- A cinematic Storm Radar (visible wind/rain, pressure rings, sweeping vortex and red critical-risk glow) and a chart storm front with a one-time impact wave. All effects respect reduced-motion preferences.
- A layered wind/rumble warning cue with opt-in audio, a dedicated preview button, and no external sound files. Audio is muted by default and never blocks market updates.
- Storm Score, margins, probability and latency headline cards.
- Three selectable spot pairs with independent price charts, correct quote precision and critical-signal markers.
- Alert panel with signal drivers and current risk status.
- Margin forecast cone with a maintenance-margin reference line.
- Exposure X-Ray and portfolio position breakdown.
- Tide-table strip.
- Interactive paper protection scenario comparison.
- Judge-vs-Coccinelle toggle with captured action timestamp.
- Replay pause/play/reset, jump-to-storm, speed 1×/5×/20× and demo scrubber.
- Two demonstration shortcuts: **Before the storm** (13:24 UTC) and **Strike moment** (USD/JPY at 13:40 UTC, a genuinely critical historical replay frame).
- Automatic backend WebSocket connection with demo fallback.

## Before the judges arrive

1. Confirm the message contract, URL, signal levels and timing fields with backend teammates.
2. Replace mock forecast/portfolio values with actual backend values and ensure all labels remain truthful.
3. Agree when the portfolio is seeded and when its margin rules apply.
4. Test play/pause/speed/jump on a full end-to-end USD/JPY stream, then check pair switching with EUR/USD and USD/SGD live ticks.
5. Make `protect` compare *identical* future scenarios with trading costs.
6. Ensure p50/p99 is computed by the real pipeline, not the demo fixture.
7. Rehearse the judge interaction and keep a local screen recording as backup.

## Regenerate authentic price fixture

If the original challenge ZIP is available outside this frontend folder:

```bash
python3 scripts/generate_fixture.py --archive ../upload/Challenge1.zip
```

This regenerates all three `src/mock/{usdjpy,eurusd,usdsgd}-2026-07-30.json` files. The source archive itself is not included with the frontend deliverable.

## Build

```bash
npm test
npm run build
```

The tests check that the three SGX fixtures use the same timestamps, spot/forward filtering is safe, the sample portfolio and risk values stay consistent across pair switches, and the UI exposes all three markets. Build output is in `dist/`. No publishing or live trading is configured.
