import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(async () => { await vite.close(); });
const { forecastMatchesTimeline, explanationMatchesAlert } = await vite.ssrLoadModule('/src/lib/timeline.js');

test('forecast timestamps respect UTC-naive backend start and the latest market tick', () => {
  assert.equal(forecastMatchesTimeline('2026-07-30T13:37:00Z', '2026-07-30T13:34', '2026-07-30T13:38:00Z'), true);
  assert.equal(forecastMatchesTimeline('2026-07-30T13:40:00Z', '2026-07-30T13:34', '2026-07-30T13:38:00Z'), false);
  assert.equal(forecastMatchesTimeline('2026-07-30T13:20:00Z', '2026-07-30T13:34', '2026-07-30T13:38:00Z'), false);
  assert.equal(forecastMatchesTimeline('2026-07-30T13:37:00Z', '2026-07-30T13:34', null), false);
  assert.equal(forecastMatchesTimeline('not-a-date', '2026-07-30T13:34', '2026-07-30T13:38:00Z'), false);
});

test('late AI explanation cannot replace a newer alert', () => {
  const latest = { ts: '2026-07-30T13:38:00Z', pair: 'USDJPY', level: 'red' };
  assert.equal(explanationMatchesAlert(latest, { ...latest }), true);
  assert.equal(explanationMatchesAlert(latest, { ...latest, ts: '2026-07-30T13:37:00Z' }), false);
  assert.equal(explanationMatchesAlert(latest, { ...latest, level: 'amber' }), false);
  assert.equal(explanationMatchesAlert(latest, { ...latest, pair: 'EURUSD' }), false);
  assert.equal(explanationMatchesAlert(null, latest), false);
});
