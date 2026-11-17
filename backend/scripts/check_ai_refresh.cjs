// Reuse the existing feed simulation and JSX compilation smoke harness.
require('../../.check/smoke_slice4.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const assets = JSON.parse(fs.readFileSync(path.join(root, 'backend/data/assets.json'), 'utf8')).assets;
const markets = JSON.parse(fs.readFileSync(path.join(root, 'backend/data/markets.json'), 'utf8')).markets;
const feed = global.__LIVE_INTERNAL__;
const current = Date.parse('2026-11-17T02:01:00Z') / 1000;
const point = { asset_id: 'OPENAI', exchange: 'okx', token_price: 125,
  implied_fdv: 1250e9, calibration_status: 'calibrated', confidence: 80, timestamp: current };
feed.handleSnapshot({ assets, markets, history: { OPENAI: { okx: [
  { ...point, timestamp: current - 86400, implied_fdv: 125e9 }, { ...point, implied_fdv: 125e9, implied_share_price: 52.0833 }
] } } });
assert.deepEqual(window.SERIES.OPENAI.okx, [1250e9]);
assert.equal(window.ASSETS.find(a => a.id === "OPENAI").consensusFDV, 1250e9);
assert.equal(window.ASSETS.find(a => a.id === 'OPENAI').lastRound.valuation, 852e9);
assert.equal(window.ASSETS.find(a => a.id === 'OPENAI').lastRound.price, null);
assert.equal(window.LISTINGS.OPENAI[0].valuationBasis, 10e9);
const initialListings = [...window.LISTINGS.OPENAI];
assert(window.DISABLED.ANTHROPIC.some(m => m.ex === 'bybit' && m.quoteOnly));
const events = [];
global.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options?.detail; } };
window.dispatchEvent = e => events.push(e);
localStorage.setItem('preIpoAlerts', JSON.stringify([
  { id: 'current-ref', assetId: 'OPENAI', kind: 'premium-above', value: 50 }
]));
feed.handleTick(point); // 1250 / 852 - 1 < 50%; the obsolete 500B anchor would fire.
assert.equal(events.filter(e => e.type === 'pre-ipo:alert').length, 0);
feed.handleTick({ ...point, token_price: 130, implied_fdv: 1300e9 });
assert.equal(events.filter(e => e.type === 'pre-ipo:alert').length, 1);
assert.equal(window.ASSETS.find(a => a.id === 'OPENAI').consensusFDV, 1300e9);
// Verify the rendered matrix's calculation independently of the feed consensus.
const app = fs.readFileSync(path.join(root, 'app.jsx'), 'utf8');
const start = app.indexOf('function normalize(listings, fdvShares)');
const end = app.indexOf('\n}', start) + 2;
const normalize = new Function(app.slice(start, end) + '; return normalize;')();
assert.equal(normalize(initialListings, 2.4e9).rows[0].impliedFDV, 1250e9);
assert.equal(normalize(initialListings, 2.4e9).rows[0].impliedShare, null);
const norm = normalize(window.LISTINGS.OPENAI, 2.4e9);
assert.equal(norm.rows[0].impliedFDV, 1300e9);
assert.equal(norm.rows[0].impliedShare, null);
console.log('PASS: new venue visibility, history cutoff, current funding reference, feed and matrix valuation');

// A stalled feed must lose expired pre-rebase cards without another server tick.
const realNow = Date.now;
let clock = Date.parse('2026-11-17T03:00:00Z');
Date.now = () => clock;
const timers = [];
global.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
const anthHistory = {};
for (const exchange of ['binance', 'mexc', 'bitget-futures']) {
  anthHistory[exchange] = [{ ...point, asset_id: 'ANTHROPIC', exchange, timestamp: clock / 1000,
    token_price: 1300, implied_fdv: 1300e9, funding_rate: 0.00005 }];
}
// Characterize the prior lifecycle independently of today's successor metadata.
const legacyMarkets = markets.map(m => m.asset_id === 'ANTHROPIC' && ['binance', 'mexc', 'bitget-futures'].includes(m.exchange)
  ? { ...m, valuation_basis_shares: 1e9, rules_reviewed_at: '2026-11-17T02:00:00Z',
      valuation_valid_until: `2026-11-17T${m.exchange === 'bitget-futures' ? '09' : '08'}:30:00Z` } : m);
feed.handleSnapshot({ assets, markets: legacyMarkets, history: { ANTHROPIC: anthHistory } });
assert.equal(window.LISTINGS.ANTHROPIC.length, 3);
feed.handleTick({ ...anthHistory.binance[0], timestamp: Date.parse('2026-11-17T08:30:00Z') / 1000, token_price: 9999 });
assert.equal(window.LISTINGS.ANTHROPIC.find(m => m.ex === 'binance').tokenPrice, 1300);
const firstDeadline = timers.find(t => t.delay > 1000);
assert(firstDeadline, 'Current markets must schedule a cutoff even if feed stalls');
clock = Date.parse('2026-11-17T08:30:00Z');
firstDeadline.callback();
assert.deepEqual(window.LISTINGS.ANTHROPIC.map(m => m.ex), ['bitget-futures']);
assert(!window.SERIES.ANTHROPIC.binance && !window.FUNDING.ANTHROPIC.binance);
assert(window.DISABLED.ANTHROPIC.some(m => m.ex === 'binance' && m.reason.includes('expired')));
const secondDeadline = timers.filter(t => t.delay > 1000).at(-1);
clock = Date.parse('2026-11-17T09:30:00Z');
secondDeadline.callback();
assert.equal(window.LISTINGS.ANTHROPIC.length, 0);
assert.equal(window.ASSETS.find(a => a.id === 'ANTHROPIC').consensusFDV, 0);
feed.handleTick(anthHistory.binance[0]);
assert.equal(window.LISTINGS.ANTHROPIC.length, 0, 'Queued ticks cannot revive expired contracts');
Date.now = realNow;
console.log('PASS: three current Anthropic cards, exact cutoff, stalled-feed purge and no queued-tick revival');

// Quote-only prices must be visible but never affect valuations, charts or alerts.
clock = Date.parse('2026-11-17T11:00:00Z');
Date.now = () => clock;
const raw = { ...point, exchange: 'bydfi', timestamp: clock / 1000, token_price: 1200,
  implied_fdv: 999e12, calibration_status: 'uncalibrated' };
feed.handleSnapshot({ assets, markets, history: {} });
const priorAlerts = events.filter(e => e.type === 'pre-ipo:alert').length;
feed.handleTick(raw);
assert.equal(window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote.tokenPrice, 1200);
assert(!(window.LISTINGS.OPENAI || []).some(m => m.ex === 'bydfi'));
assert(!window.SERIES.OPENAI?.bydfi && !window.FUNDING.OPENAI?.bydfi);
assert.equal(window.ASSETS.find(a => a.id === 'OPENAI').consensusFDV, 0);
assert.equal(events.filter(e => e.type === 'pre-ipo:alert').length, priorAlerts);
const rawDeadline = timers.at(-1);
clock += 60000;
rawDeadline.callback();
assert(!window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote, 'Stalled quote-only feed expires without a server event');
clock -= 60000;
feed.handleTick(raw);
feed.handleStatus({ statuses: [{ market_id: 'OPENAI:bydfi', state: 'disconnected' }] });
assert(!window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote, 'Disconnected quote-only feed must not look live');
feed.handleSnapshot({ assets, markets, history: { OPENAI: { bydfi: [{ ...raw, timestamp: clock / 1000 - 3600 }] } } });
assert(!window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote, 'Old history must not become a fresh raw card');
const successorPoint = { ...raw, asset_id: 'ANTHROPIC', exchange: 'binance', token_price: 98,
  timestamp: clock / 1000, calibration_status: 'calibrated' };
feed.handleSnapshot({ assets, markets, history: { ANTHROPIC: { binance: [
  { ...successorPoint, token_price: 1300, timestamp: Date.parse('2026-11-17T08:00:00Z') / 1000 }, successorPoint
] } } });
assert.deepEqual(window.SERIES.ANTHROPIC.binance, [98 * 13116075288]);
feed.handleTick({ ...successorPoint, token_price: 1300, timestamp: Date.parse('2026-11-17T08:00:00Z') / 1000 });
assert.equal(window.LISTINGS.ANTHROPIC[0].tokenPrice, 98, 'Queued legacy prices cannot cross successor boundary');
Date.now = realNow;
console.log('PASS: quote-only visibility, valuation isolation, disconnected and stale-history suppression');

// Exercise the real socket callbacks, including close/reopen without a snapshot.
const sockets = [];
global.WebSocket = function () { sockets.push(this); };
eval(fs.readFileSync(path.join(root, 'data-live.js'), 'utf8'));
const socket = sockets.at(-1);
socket.onopen();
socket.onmessage({ data: JSON.stringify({ type: 'snapshot', assets, markets, history: {} }) });
socket.onmessage({ data: JSON.stringify({ type: 'tick', quote: { ...raw, timestamp: Date.now() / 1000 } }) });
assert(window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote);
socket.onclose();
assert(!window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote, 'Socket close must immediately remove raw cards');
timers.at(-1).callback();
sockets.at(-1).onopen();
assert(!window.DISABLED.OPENAI.find(m => m.ex === 'bydfi').rawQuote, 'Reopening socket must not revive cached quotes');
console.log('PASS: actual socket close/reopen clears raw prices until fresh data');
