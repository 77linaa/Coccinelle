# Coccinelle — Frontend (Person C / Rayane)

React + Vite + Tailwind CSS + Recharts, integrated with Helina's FastAPI API and Meryem's streaming engine. Standalone fallback remains available.

## Start it

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

The app connects to `ws://localhost:8000/ws`, automatically retries on disconnect, and switches to an **interactive local demo** when live replay ticks are unavailable. Replay controls, market chart, warning cards, margin graph and protection simulator work in demo mode.

The three selectable spot markets (USD/JPY, EUR/USD, USD/SGD) each have **91 real SGX challenge quotes from 30 July 2026, sampled every two minutes between 12:00 and 15:00 UTC**. All three markets use the same replay clock; switching pairs changes the chart and pair-specific score without resetting playback. The sample portfolio contains fictional positions in all three pairs. Its margin rules, scores, forecast probabilities and timing values in demo mode are **illustrative**. No orders are placed.

## Real backend integration (V5)

Actual FastAPI wire protocol in `backend/sentinel/api.py`:

- WebSocket `type: "hello"` provides replay state, fictional portfolio and optional forecast.
- WebSocket `type: "tick"` puts `ts`, `mid`, `pair`, `tenor`, `score`, `level`, `spread_bps`, `latency_us` inside **`market`**, plus separate `portfolio` (sometimes null) and `backend_ms` fields.
- `green/yellow/amber/red` are mapped to `LOW/ELEVATED/HIGH/CRITICAL`.
- `type: "forecast"` is **separate** from ticks; its percentile arrays are downsampled to seven points without inventing forecasts.
- Forecast timestamps are checked against the currently replayed UTC window before display; an old in-flight model result from a previous jump is discarded. Late AI explanations can only attach to their matching level-change alert.
- `type: "alert"` and `type: "explanation"` are consumed separately, including template or AI-generated plain-English alert text.
- Real backend p50/p99 values are derived from the last 200 price-processing latency observations. They are not network round-trip times.
- FastAPI does **not** consume WebSocket commands. Play, pause, speed, jump, `/simulate` (read-only preview), and `/protect` (paper judge action) use HTTP `POST`.
- The Human vs Coccinelle outcome panel uses the backend's `GET /protect`, WebSocket `type: "protection"` and per-tick `protection` summaries. It shows unprotected, judge and first-red-signal branches, backend-reported lowest observed margin buffers, breaches, current equity and lead time. `POST /protect/reset` lets a judge try again on the same timeline.
- The backend cannot run a Coccinelle branch until the judge selects the action to compare. The comparison is **retrospective and paper-only**, not proof of a fully autonomous hedge or a real trade. If no red signal has occurred, the panel explicitly shows a pending state.

Each pair has its own spot quote buffer. **Currently the backend emits USD/JPY only by default**; the other two selectable pairs retain their authentic SGX historical frontend replay. In `SENTINEL_SOURCE=mock` mode, USD/JPY is visibly labelled synthetic. No live orders are placed. Backend portfolio snapshots publish aggregate accounting but not position breakdown; the UI explicitly says so instead of showing fictional extra positions as if they came from the backend. The backend may also have no forecasts until Parquet files are generated.

For a quick Windows PowerShell integrated run from the repo root:

```powershell
py -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:SENTINEL_SOURCE = "mock"
$env:SENTINEL_AI = "off"
.\backend\.venv\Scripts\python.exe -m uvicorn sentinel.api:app --app-dir backend --reload
```

In **another PowerShell terminal**:

```powershell
cd frontend
npm ci
npm run dev
```

With processed data in `data/processed`, switch `$env:SENTINEL_SOURCE = "engine"` and restart the backend. Use `python -m engine.data_loader --zip path\to\Challenge1.zip` to generate the ignored Parquet files. Test `http://localhost:8000/health` before judging. Default frontend URLs are WebSocket `ws://localhost:8000/ws` and HTTP `http://localhost:8000`; override via `VITE_COCCINELLE_WS_URL` and `VITE_COCCINELLE_API_URL` in `.env` when necessary.

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
- Interactive paper protection scenario comparison; HTTP risk-officer previews in backend mode.
- Responsive Human vs Coccinelle three-account survival comparison with honest waiting/veto/incomplete-replay states and reset.
- Judge-vs-Coccinelle toggle with captured action timestamp.
- Replay pause/play/reset, jump-to-storm, speed 1×/5×/20× and demo scrubber.
- Two demonstration shortcuts: **Before the storm** (13:34 UTC) and **Strike moment** (USD/JPY at **13:38 UTC**, the real SGX engine's first red signal on 30 July 2026, score 88.2). Level changes at 13:36 yellow, 13:37 amber and 13:38 red in the engine stream; frontend fixture scores are illustrative and do not use the identical scoring algorithm.
- Automatic backend WebSocket connection, retry, HTTP replay controls, and demo fallback.

## Before the judges arrive

1. Ask Meryem to generate `data/processed` from the SGX archive, otherwise the engine and Monte Carlo cannot run.
2. Confirm Helina's portfolio is USD/JPY-only and that the backend forecast and Risk Officer have sufficiently warmed up.
3. Test play/pause/speed/jump in the browser with backend actually running. The frontend's 1× / 5× / 20× labels correspond to 60× / 300× / 1200× historical clock speed.
4. If time permits, have Helina include `exposure` summaries in backend portfolio snapshots and Meryem expand the backend stream to all three spot pairs. Until then the UI labels limitations explicitly.
5. Test `/simulate` and `/protect`, including a 21:00 UTC forced-sale veto, before demonstrating Judge vs Coccinelle. In Judge Mode, choose a reduction/close, press Protect, then let the replay cross a **real backend red signal**; the third branch appears once computed. A frontend fixture's "Strike moment" does not guarantee a backend red signal at exactly the same timestamp.
6. Rehearse the judge interaction and keep a local screen recording as backup.

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

The tests check the three SGX fixtures, UI navigation, the real FastAPI tick/forecast contract, HTTP paper-action shape, survival-card states, stale forecast rejection and late-explanation gating. Build output is in `dist/`. No publishing or live trading is configured.
