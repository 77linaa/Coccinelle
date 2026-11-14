import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(async () => { await vite.close(); });
const {
  API_URL, levelFromBackend, normaliseFrame, normalisePortfolio,
  normaliseForecast, postBackend, getBackend,
} = await vite.ssrLoadModule('/src/lib/protocol.js');

test('real Helina tick envelope converts price, spot level, portfolio and measured latency', () => {
  const tick = normaliseFrame({
    type: 'tick', source: 'engine', backend_ms: 1.5,
    market: { ts: '2026-07-30T13:40:00Z', pair: 'USDJPY', tenor: 'SPOT',
      mid: 155, spread_bps: 2, score: 94, level: 'red', latency_us: 250 },
    portfolio: { equity: 10000, maintenance_margin: 4000, margin_buffer: 6000,
      unrealised_pnl: -150, notional: 200000, leverage: 20 },
  });
  assert.equal(tick.timestamp, '2026-07-30T13:40:00Z');
  assert.equal(tick.pair, 'USDJPY');
  assert.equal(tick.storm.level, 'CRITICAL');
  assert.equal(tick.storm.score, 94);
  assert.ok(Math.abs(tick.quote.bid - 154.9845) < 1e-8);
  assert.ok(Math.abs(tick.quote.ask - 155.0155) < 1e-8);
  assert.equal(tick.portfolio.pnl, -150);
  assert.equal(tick.portfolio.buffer, 6000);
  assert.equal(tick.portfolio.breakdownAvailable, false);
  assert.equal(tick.latency.latestMs, 1.75);
  assert.equal(tick.forecast, null);
});

test('separate messages never masquerade as market ticks', () => {
  for (const type of ['hello', 'forecast', 'replay', 'alert', 'explanation', 'protection']) {
    assert.equal(normaliseFrame({ type, market: { ts: '2026-07-30T13:30:00Z', mid: 150 } }), null);
  }
  assert.equal(normaliseFrame({ type: 'tick', market: { ts: '2026-07-30T13:30:00Z', mid: 0 } }), null);
  assert.equal(normalisePortfolio(null), null);
});

test('backend green/yellow/amber/red all map to dashboard risk levels', () => {
  assert.deepEqual(['green', 'yellow', 'amber', 'red'].map(levelFromBackend),
    ['LOW', 'ELEVATED', 'HIGH', 'CRITICAL']);
});

test('actual percentile cone becomes a truthful 24h seven-point chart', () => {
  const values = Array.from({ length: 288 }, (_, i) => 120000 - i * 20);
  const model = normaliseForecast({ type: 'forecast', forecast: {
    breach_probability: 0.375, horizon_hours: 24,
    cone: { step_minutes: 5, p5: values.map((v) => v - 1000),
      p50: values, p95: values.map((v) => v + 1000) },
  } }, 120000);
  assert.equal(model.breachProbability, 0.375);
  assert.equal(model.cone.length, 7);
  assert.equal(model.cone[0].hour, '0h');
  assert.equal(model.cone.at(-1).hour, '24h');
  assert.equal(model.cone.at(-1).median, values[287]);
  assert.equal(model.cone.at(-1).lower, values[287] - 1000);
});

test('paper action uses HTTP POST rather than ignored websocket commands', async () => {
  let requested = null;
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    requested = { url, options };
    return { ok: true, json: async () => ({ review: { approved: true } }) };
  };
  try {
    const result = await postBackend('/simulate', { kind: 'reduce', pair: 'USDJPY', percent: 50 });
    assert.equal(requested.url, `${API_URL}/simulate`);
    assert.equal(requested.options.method, 'POST');
    assert.equal(JSON.parse(requested.options.body).percent, 50);
    assert.equal(result.review.approved, true);
  } finally {
    globalThis.fetch = original;
  }
});

test('protection race state can be read without mutating it', async () => {
  const original = globalThis.fetch;
  let requested;
  globalThis.fetch = async (url, options) => {
    requested = { url, options };
    return { ok: true, json: async () => ({ red_at: null, judge: null, sentinel: null }) };
  };
  try {
    const race = await getBackend('/protect');
    assert.equal(requested.url, `${API_URL}/protect`);
    assert.equal(requested.options, undefined);
    assert.equal(race.judge, null);
  } finally {
    globalThis.fetch = original;
  }
});
