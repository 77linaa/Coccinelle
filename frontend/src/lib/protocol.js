/**
 * Frontend/backend contract (provisional until Meryem + Helina confirm it).
 * All time fields are UTC ISO-8601 strings, money values USD unless specified.
 * Price signals and margin probabilities must be provided by the backend in live mode.
 */
export const SOCKET_URL = import.meta.env.VITE_COCCINELLE_WS_URL || 'ws://localhost:8000/ws';

const numberOr = (value, fallback = null) => {
  const n = Number(value);
  return value == null || !Number.isFinite(n) ? fallback : n;
};

export function normaliseFrame(raw) {
  const body = raw?.payload || raw?.data || raw;
  if (!body || typeof body !== 'object') return null;
  if (body.type === 'heartbeat' || body.type === 'ack') return null;

  const quote = body.quote || {};
  const storm = body.storm || body.signal || {};
  const portfolio = body.portfolio || {};
  const forecast = body.forecast || {};
  const latency = body.latency || {};
  const timestamp = body.timestamp || body.time || quote.timestamp;
  const mid = numberOr(quote.mid ?? body.mid ?? body.close_mid ?? body.price);
  if (!timestamp || mid == null) return null;

  return {
    type: 'tick', timestamp, pair: body.pair || quote.pair || 'USDJPY',
    product: body.product || body.product_type || body.instrument_type || 'SPOT',
    tenor: body.tenor || 'SPOT',
    quote: {
      mid, bid: numberOr(quote.bid ?? body.bid ?? body.close_bid, mid),
      ask: numberOr(quote.ask ?? body.ask ?? body.close_ask, mid),
    },
    storm: {
      score: numberOr(storm.score ?? body.storm_score, 0),
      level: storm.level ?? body.level ?? 'LOW',
      drivers: storm.drivers || body.drivers || [],
    },
    portfolio: {
      equity: numberOr(portfolio.equity),
      maintenanceMargin: numberOr(portfolio.maintenanceMargin ?? portfolio.maintenance_margin ?? portfolio.required_margin),
      buffer: numberOr(portfolio.buffer ?? portfolio.margin_buffer),
      pnl: numberOr(portfolio.pnl ?? portfolio.unrealized_pnl),
      notional: numberOr(portfolio.notional),
      leverage: numberOr(portfolio.leverage),
      exposure: portfolio.exposure || [],
    },
    forecast: {
      breachProbability: numberOr(forecast.breachProbability ?? forecast.breach_probability),
      horizonHours: numberOr(forecast.horizonHours ?? forecast.horizon_hours, 24),
      cone: forecast.cone || [],
    },
    latency: {
      latestMs: numberOr(latency.latestMs ?? latency.total_ms ?? body.latency_ms),
      medianMs: numberOr(latency.medianMs ?? latency.median_ms ?? latency.p50_ms),
      p99Ms: numberOr(latency.p99Ms ?? latency.p99_ms),
    },
    tideTable: body.tideTable || body.tide_table || [],
    alert: body.alert || null,
  };
}

export function makeCommand(type, payload = {}) {
  return JSON.stringify({ type, ...payload });
}
