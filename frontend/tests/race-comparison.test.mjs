import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { createServer } from 'vite';

const vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(async () => { await vite.close(); });
const { default: RaceComparison } = await vite.ssrLoadModule('/src/components/RaceComparison.jsx');

const base = {
  red_at: '2026-07-30T13:30:00Z',
  action: { kind: 'reduce', pair: 'USDJPY', percent: 50 },
  unprotected: { min_buffer: -5000, survived: false, portfolio: { equity: 93000 } },
  judge: { min_buffer: 1000, survived: true, acted_at: '2026-07-30T13:42:00Z',
    portfolio: { equity: 99000 }, vs_unprotected: 6000 },
  sentinel: { min_buffer: 3000, survived: true, acted_at: '2026-07-30T13:30:00Z',
    portfolio: { equity: 101000 }, vs_unprotected: 8000 },
  sentinel_lead_minutes: 12,
  sentinel_blocked: null,
  replay_exact: true,
};

test('survival view renders all three real backend branches and exact lead', () => {
  const html = renderToString(createElement(RaceComparison, {
    race: base, backendSource: 'engine', onReset: () => {},
  }));
  assert.ok(html.includes('UNPROTECTED'));
  assert.ok(html.includes('THE JUDGE'));
  assert.ok(html.includes('COCCINELLE'));
  assert.ok(html.includes('MARGIN BREACH'));
  assert.ok(html.includes('NO BREACH OBSERVED'));
  assert.ok(html.includes('12.0 market minutes before'));
  assert.ok(html.includes('−$5,000'));
  assert.ok(html.includes('+$8,000'));
  assert.ok(html.includes('not a real or autonomous order'));
});

test('unobserved red event and pending judge are visibly pending', () => {
  const html = renderToString(createElement(RaceComparison, {
    race: { ...base, red_at: null, action: null, judge: null, sentinel: null, sentinel_lead_minutes: null },
    backendSource: 'mock', onReset: () => {},
  }));
  assert.ok(html.includes('AWAITING RED SIGNAL'));
  assert.ok(html.includes('NO ACTION YET'));
  assert.ok(html.includes('SYNTHETIC API FEED'));
  assert.ok(!html.includes('market minutes before'));
});

test('veto and incomplete replay are explicitly disclosed', () => {
  const html = renderToString(createElement(RaceComparison, {
    race: { ...base, sentinel: null, sentinel_blocked: { approved: false, reasons: ['wide spread'] }, replay_exact: false },
    backendSource: 'engine', onReset: () => {},
  }));
  assert.ok(html.includes('RISK OFFICER VETO'));
  assert.ok(html.includes('wide spread'));
  assert.ok(html.includes('comparison is incomplete'));
});

test('race view supports missing snapshot without invented numbers', () => {
  const html = renderToString(createElement(RaceComparison, {
    race: null, backendSource: 'engine', onReset: () => {},
  }));
  assert.ok(html.includes('Waiting for the first backend risk snapshot'));
  assert.ok(!html.includes('−$5,000'));
});
