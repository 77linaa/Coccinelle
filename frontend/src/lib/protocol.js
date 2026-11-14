/**
 * FastAPI's actual wire contract. A replay is historical market time, not a live
 * exchange connection. All money fields are USD; protection is paper-only.
 */
export const SOCKET_URL = import.meta.env.VITE_COCCINELLE_WS_URL || 'ws://localhost:8000/ws';
export const API_URL = import.meta.env.VITE_COCCINELLE_API_URL
  || SOCKET_URL.replace(/^ws(s?):/, 'http$1:').replace(/\/ws\/?$/, '');

const numberOr = (value, fallback = null) => {
  const n = Number(value);
  return value == null || !Number.isFinite(n) ? fallback : n;
};

export const levelFromBackend = (value) => ({
  green: 'LOW', yellow: 'ELEVATED', amber: 'HIGH', red: 'CRITICAL',
})[String(value || '').toLowerCase()] || String(value || 'LOW').toUpperCase();

export function normalisePortfolio(portfolio) {
  if (!portfolio || typeof portfolio !== 'object') return null;
  return {
    equity: numberOr(portfolio.equity),
    maintenanceMargin: numberOr(portfolio.maintenanceMargin ?? portfolio.maintenance_margin ?? portfolio.required_margin),
    buffer: numberOr(portfolio.buffer ?? portfolio.margin_buffer),
    pnl: numberOr(portfolio.pnl ?? portfolio.unrealised_pnl ?? portfolio.unrealized_pnl),
    notional: numberOr(portfolio.notional),
    leverage: numberOr(portfolio.leverage),
    exposure: Array.isArray(portfolio.exposure) ? portfolio.exposure : [],
    breakdownAvailable: Array.isArray(portfolio.exposure),
  };
}

/** Reduce the API's 288-point percentile arrays to seven truthful time points. */
function normaliseCone(raw, currentEquity) {
  if (Array.isArray(raw)) return raw;
  if (!raw || !Array.isArray(raw.p5) || !Array.isArray(raw.p50) || !Array.isArray(raw.p95)) return [];
  const length = Math.min(raw.p5.length, raw.p50.length, raw.p95.length);
  if (!length) return [];
  const stepMinutes = numberOr(raw.step_minutes, 5);
  const samples = Array.from({ length: 6 }, (_, k) => Math.min(length - 1, Math.ceil((k + 1) * length / 6) - 1));
  const cone = Number.isFinite(currentEquity)
    ? [{ hour: '0h', lower: currentEquity, median: currentEquity, upper: currentEquity }]
    : [];
  for (const i of [...new Set(samples)]) {
    const hours = Math.round((i + 1) * stepMinutes / 60 * 10) / 10;
    cone.push({ hour: `${hours}h`, lower: raw.p5[i], median: raw.p50[i], upper: raw.p95[i] });
  }
  return cone;
}

export function normaliseForecast(body, currentEquity = null) {
  // "forecast" events wrap the model; "hello" may carry the entire event.
  const forecast = body?.forecast && !('breach_probability' in body) ? body.forecast : body;
  if (!forecast || typeof forecast !== 'object') return null;
  return {
    breachProbability: numberOr(forecast.breachProbability ?? forecast.breach_probability),
    horizonHours: numberOr(forecast.horizonHours ?? forecast.horizon_hours, 24),
    cone: normaliseCone(forecast.cone, currentEquity),
  };
}

export function normaliseFrame(raw) {
  const body = raw?.payload || raw?.data || raw;
  if (!body || typeof body !== 'object' || (body.type && body.type !== 'tick')) return null;
  const market = body.market || {};
  const quote = body.quote || {};
  const storm = body.storm || body.signal || {};
  const timestamp = body.timestamp || body.time || quote.timestamp || market.ts;
  const mid = numberOr(quote.mid ?? market.mid ?? body.mid ?? body.close_mid ?? body.price);
  if (!timestamp || mid == null || mid <= 0) return null;

  const spread = numberOr(market.spread_bps);
  const halfSpread = spread == null ? 0 : mid * spread / 20000;
  const score = numberOr(storm.score ?? market.score ?? body.storm_score, 0);
  const drivers = storm.drivers || body.drivers || (body.market ? [
    `Backend storm score: ${score.toFixed(1)}/100`,
    ...(spread == null ? [] : [`Market spread: ${spread.toFixed(2)} bps`]),
  ] : []);
  const portfolio = normalisePortfolio(body.portfolio);
  const forecast = normaliseForecast(body.forecast, portfolio?.equity);
  const latency = body.latency || {};
  const latestMs = body.market
    ? numberOr(market.latency_us, 0) / 1000 + numberOr(body.backend_ms, 0)
    : numberOr(latency.latestMs ?? latency.total_ms ?? body.latency_ms);

  return {
    type: 'tick', timestamp, pair: body.pair || quote.pair || market.pair || 'USDJPY',
    product: body.product || body.product_type || body.instrument_type || 'SPOT',
    tenor: body.tenor || market.tenor || 'SPOT',
    quote: {
      mid, bid: numberOr(quote.bid ?? body.bid ?? body.close_bid, mid - halfSpread),
      ask: numberOr(quote.ask ?? body.ask ?? body.close_ask, mid + halfSpread),
    },
    storm: {
      score: Math.max(0, Math.min(100, score)),
      level: levelFromBackend(storm.level ?? market.level ?? body.level),
      drivers,
    },
    portfolio,
    forecast,
    latency: {
      latestMs,
      medianMs: numberOr(latency.medianMs ?? latency.median_ms ?? latency.p50_ms),
      p99Ms: numberOr(latency.p99Ms ?? latency.p99_ms),
    },
    tideTable: body.tideTable || body.tide_table || [],
    alert: body.alert || null,
  };
}

export async function postBackend(path, body = null) {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: body == null ? undefined : { 'Content-Type': 'application/json' },
    body: body == null ? undefined : JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result?.detail;
    throw new Error(typeof detail === 'string' ? detail : `Backend returned HTTP ${response.status}`);
  }
  return result;
}

export async function getBackend(path) {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json();
}
