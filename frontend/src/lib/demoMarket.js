import jpyQuotes from '../mock/usdjpy-2026-07-30.json';
import eurQuotes from '../mock/eurusd-2026-07-30.json';
import sgdQuotes from '../mock/usdsgd-2026-07-30.json';
import { MARKET_IDS, marketById } from './markets.js';

export const HISTORICAL_QUOTES = {
  USDJPY: jpyQuotes,
  EURUSD: eurQuotes,
  USDSGD: sgdQuotes,
};

export const REPLAY_LENGTH = Math.min(...MARKET_IDS.map((id) => HISTORICAL_QUOTES[id].length));

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function riskLevel(score) {
  if (score >= 80) return 'CRITICAL';
  if (score >= 60) return 'HIGH';
  if (score >= 35) return 'ELEVATED';
  return 'LOW';
}

function signal(pair, index) {
  const quotes = HISTORICAL_QUOTES[pair];
  const tick = quotes[index];
  const recent = quotes[Math.max(0, index - 8)].mid;
  const previous = quotes[Math.max(0, index - 2)].mid;
  const adversePct = Math.max(0, (recent - tick.mid) / recent) * 100;
  const shortPct = Math.abs(tick.mid / previous - 1) * 100;
  const spreadBps = ((tick.ask - tick.bid) / tick.mid) * 10000;
  const score = Math.round(clamp(
    23 + adversePct * 55 + shortPct * 19 + Math.max(0, spreadBps - 0.25) * 1.6,
    10, 98,
  ));
  return { score, adversePct, shortPct, spreadBps };
}

// Illustrative positions. These are not SGX client accounts or actual margin rules.
const POSITIONS = [
  { pair: 'USDJPY', side: 'Long USD', notional: 1_000_000, payout: 'usdBase' },
  { pair: 'EURUSD', side: 'Long EUR', notional: 350_000, payout: 'foreignBase' },
  { pair: 'USDSGD', side: 'Long USD', notional: 250_000, payout: 'usdBase' },
];

export function demoPortfolio(index) {
  const notional = POSITIONS.reduce((sum, position) => sum + position.notional, 0);
  const pnl = POSITIONS.reduce((sum, position) => {
    const opening = HISTORICAL_QUOTES[position.pair][0].mid;
    const current = HISTORICAL_QUOTES[position.pair][index].mid;
    const movement = position.payout === 'foreignBase'
      ? current / opening - 1
      : 1 - opening / current;
    return sum + position.notional * movement;
  }, 0);
  const equity = 180_000 + pnl;
  const maintenanceMargin = 125_000;
  return {
    equity, maintenanceMargin, buffer: equity - maintenanceMargin,
    pnl, notional, leverage: notional / equity,
    exposure: POSITIONS.map((position) => ({
      pair: marketById(position.pair).label.replaceAll(' ', ''),
      side: position.side, notional: position.notional,
      share: position.notional / notional,
    })),
  };
}

export function demoFrame(pair, rawIndex) {
  if (!MARKET_IDS.includes(pair)) throw new Error(`Unsupported demo pair: ${pair}`);
  const index = clamp(rawIndex, 0, REPLAY_LENGTH - 1);
  const tick = HISTORICAL_QUOTES[pair][index];
  const { score, adversePct, shortPct, spreadBps } = signal(pair, index);
  const overallScore = Math.max(...MARKET_IDS.map((id) => signal(id, index).score));
  const portfolio = demoPortfolio(index);
  const breachProbability = clamp(
    0.03 + Math.max(0, 55_000 - portfolio.buffer) / 95_000 + overallScore / 650,
    0.03, 0.95,
  );
  const cone = Array.from({ length: 7 }, (_, i) => {
    const hour = i * 4;
    const spread = Math.sqrt(hour / 24) * (4_500 + overallScore * 110);
    return {
      hour: `${hour}h`, lower: Math.max(0, portfolio.equity - spread),
      median: portfolio.equity - (overallScore / 100) * hour * 60,
      upper: portfolio.equity + spread * 0.88,
    };
  });
  return {
    type: 'tick', timestamp: tick.timestamp, pair, product: 'SPOT', tenor: 'SPOT',
    quote: { mid: tick.mid, bid: tick.bid, ask: tick.ask },
    storm: {
      score, level: riskLevel(score),
      drivers: [
        `${adversePct.toFixed(2)}% adverse 16-minute move`,
        `${shortPct.toFixed(2)}% absolute 4-minute move`,
        `Bid/ask spread ${spreadBps.toFixed(2)} bps`,
      ],
    },
    portfolio,
    forecast: { breachProbability, horizonHours: 24, cone },
    latency: { latestMs: 12 + (index % 7), medianMs: 14, p99Ms: 27 },
    tideTable: [
      { hour: '12:00', risk: 'MODERATE' },
      { hour: '13:00', risk: 'HIGH' },
      { hour: '14:00', risk: 'HIGH' },
      { hour: '15:00', risk: 'ELEVATED' },
    ],
  };
}
