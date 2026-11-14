export const MARKETS = [
  { id: 'USDJPY', label: 'USD / JPY', digits: 4, axisDigits: 2 },
  { id: 'EURUSD', label: 'EUR / USD', digits: 6, axisDigits: 4 },
  { id: 'USDSGD', label: 'USD / SGD', digits: 6, axisDigits: 4 },
];

export const MARKET_IDS = MARKETS.map((market) => market.id);

export function normalisePair(value) {
  return String(value || '').replace(/[^a-zA-Z]/g, '').toUpperCase();
}

export function marketById(pair) {
  return MARKETS.find((market) => market.id === normalisePair(pair)) || MARKETS[0];
}

export function isSupportedSpot(frame) {
  const pair = normalisePair(frame?.pair);
  return MARKET_IDS.includes(pair)
    && String(frame?.product || 'SPOT').toUpperCase() === 'SPOT'
    && String(frame?.tenor || 'SPOT').toUpperCase() === 'SPOT';
}
