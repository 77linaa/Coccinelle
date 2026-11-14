import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { createServer } from 'vite';

const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
after(async () => { await vite.close(); });

const { demoFrame, HISTORICAL_QUOTES, REPLAY_LENGTH } = await vite.ssrLoadModule('/src/lib/demoMarket.js');
const { MARKET_IDS, isSupportedSpot, normalisePair } = await vite.ssrLoadModule('/src/lib/markets.js');
const { sectionAtViewport } = await vite.ssrLoadModule('/src/hooks/useActiveSection.js');

test('all three authentic spot fixtures share one replay clock', () => {
  assert.equal(REPLAY_LENGTH, 91);
  for (let index = 0; index < REPLAY_LENGTH; index += 1) {
    const timestamps = MARKET_IDS.map((pair) => HISTORICAL_QUOTES[pair][index].timestamp);
    assert.equal(new Set(timestamps).size, 1, `different timestamps at replay index ${index}`);
  }
});

test('spot pair filter prevents cross-pair and forward-tenor chart contamination', () => {
  assert.equal(normalisePair('EUR/USD'), 'EURUSD');
  for (const pair of MARKET_IDS) {
    assert.equal(isSupportedSpot({ pair, product: 'SPOT', tenor: 'SPOT' }), true);
  }
  assert.equal(isSupportedSpot({ pair: 'EURUSD', product: 'FORWARD', tenor: '1W' }), false);
  assert.equal(isSupportedSpot({ pair: 'GBPUSD', product: 'SPOT', tenor: 'SPOT' }), false);
});

test('risk and sample portfolio metrics remain finite for all markets', () => {
  for (let index = 0; index < REPLAY_LENGTH; index += 1) {
    const frames = MARKET_IDS.map((pair) => demoFrame(pair, index));
    const [portfolio] = frames.map((frame) => frame.portfolio);
    const expectedTime = frames[0].timestamp;
    for (const frame of frames) {
      assert.equal(frame.timestamp, expectedTime);
      assert.ok(frame.quote.mid > 0);
      assert.ok(frame.storm.score >= 0 && frame.storm.score <= 100);
      assert.equal(frame.portfolio.exposure.length, 3);
      assert.ok(Number.isFinite(frame.portfolio.buffer));
      assert.ok(frame.forecast.breachProbability >= 0 && frame.forecast.breachProbability <= 1);
      assert.equal(frame.portfolio.buffer, portfolio.buffer);
      assert.equal(frame.forecast.breachProbability, frames[0].forecast.breachProbability);
    }
  }
});

test('the one-click USD/JPY strike marker leads to a real critical replay frame', () => {
  const index = HISTORICAL_QUOTES.USDJPY.findIndex((quote) => quote.timestamp === '2026-07-30T13:40:00Z');
  assert.ok(index >= 0);
  const strike = demoFrame('USDJPY', index);
  assert.equal(strike.storm.level, 'CRITICAL');
  assert.ok(strike.storm.score >= 80);
});

test('dashboard renders an accessible selector for all three markets', async () => {
  const { default: App } = await vite.ssrLoadModule('/src/App.jsx');
  const html = renderToString(createElement(App));
  assert.equal((html.match(/class="watch-pair /g) || []).length, 3);
  for (const label of ['USD / JPY', 'EUR / USD', 'USD / SGD']) {
    assert.ok(html.includes(label));
  }
  assert.ok(!html.includes('SOON'));
  assert.ok(html.includes('aria-current="location"'));
  assert.ok(html.includes('storm-atmosphere'));
});

test('active sidebar section tracks the section in the reading area', () => {
  const sectionTops = (market, portfolio, protection) => (id) => ({
    market, portfolio, protection,
  })[id];
  assert.equal(sectionAtViewport(sectionTops(360, 880, 1450), 900), 'overview');
  assert.equal(sectionAtViewport(sectionTops(25, 460, 1000), 900), 'market');
  assert.equal(sectionAtViewport(sectionTops(-650, 30, 620), 900), 'portfolio');
  assert.equal(sectionAtViewport(sectionTops(-1200, -590, 40), 900), 'protection');
  assert.equal(sectionAtViewport(sectionTops(-1200, -590, 370), 900, true), 'protection');
});
