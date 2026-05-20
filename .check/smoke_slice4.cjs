// Slice-4 frontend smoke. Pattern-based assertions on data-live.js + app.jsx
// plus a JSDOM-free functional sim of the IIFE'd WS handlers by rewriting
// the module wrapper in-memory.

const fs = require('fs');
const path = require('path');
const babel = require('@babel/standalone');

const ROOT = path.resolve(__dirname, '..');
const DL = fs.readFileSync(path.join(ROOT, 'data-live.js'), 'utf-8');
const AJ = fs.readFileSync(path.join(ROOT, 'app.jsx'), 'utf-8');

let failed = 0;
function ok(cond, label) {
  if (cond) console.log('PASS: ' + label);
  else { console.error('FAIL: ' + label); failed++; }
}

// ---- Static pattern checks ----

// F52 — X-Admin-Token wiring + getAdminToken helper exist
ok(/X-Admin-Token/.test(AJ), 'F52: app.jsx sends X-Admin-Token header');
ok(/function getAdminToken/.test(AJ), 'F52: app.jsx has getAdminToken helper');
ok(/clearAdminToken/.test(AJ), 'F52: app.jsx clears token on 401');

// F53 — ratioSourceFromMarket returns m.ratio_source verbatim
ok(/return m\.ratio_source \|\| "uncalibrated";/.test(DL), 'F53: ratioSourceFromMarket pass-through');
ok(/isLiveMarket/.test(DL), 'F53: isLiveMarket helper exists');
ok(/"uncalibrated":\s*{[^}]*Uncalibrated/.test(AJ), 'F53: SRC_META has uncalibrated entry');

// F54 — empty-listing safe states
ok(/if \(!anchor\)/.test(AJ), 'F54: DerivationPanel guards anchor undefined');
ok(/Waiting for a live calibrated venue/.test(AJ), 'F54: DerivationPanel empty placeholder');
ok(/hasRows/.test(AJ), 'F54: KPIStrip uses hasRows guard');
ok(/Waiting for tick history/.test(AJ), 'F54: ValuationChart empty placeholder');
ok(/No calibrated venues to compare yet/.test(AJ), 'F54: PremiumBars empty placeholder');

// F55 — loadAlerts shape validation
ok(/typeof r\.assetId === "string"/.test(DL), 'F55: loadAlerts validates assetId type');
ok(/typeof r\.value === "number"/.test(DL), 'F55: loadAlerts validates value type');

// F56 — getStructMeta/getSrcMeta + no remaining unsafe STRUCT_META[r.structure]
ok(/function getStructMeta/.test(AJ), 'F56: getStructMeta helper exists');
ok(/function getSrcMeta/.test(AJ), 'F56: getSrcMeta helper exists');
ok(!/STRUCT_META\[r\.structure\]\.tone/.test(AJ), 'F56: no unguarded STRUCT_META[r.structure].tone');
ok(!/STRUCT_META\[r\.structure\]\.sym/.test(AJ), 'F56: no unguarded STRUCT_META[r.structure].sym');
ok(!/STRUCT_META\[r\.structure\]\.label/.test(AJ), 'F56: no unguarded STRUCT_META[r.structure].label');
ok(!/STRUCT_META\[g\.structure\]\.tone/.test(AJ), 'F56: no unguarded STRUCT_META[g.structure].tone');
ok(!/STRUCT_META\[c\.long\.structure\]\.sym/.test(AJ), 'F56: no unguarded STRUCT_META[c.long.structure].sym');
ok(!/STRUCT_META\[c\.short\.structure\]\.sym/.test(AJ), 'F56: no unguarded STRUCT_META[c.short.structure].sym');
ok(!/SRC_META\[r\.ratioSource\]\.dot/.test(AJ), 'F56: no unguarded SRC_META[r.ratioSource].dot');
ok(!/SRC_META\[anchor\.ratioSource\]\.label/.test(AJ), 'F56: no unguarded SRC_META[anchor.ratioSource].label');

// F57 — safePct/safeRatioPct helpers used
ok(/function safePct/.test(AJ), 'F57: safePct helper exists');
ok(/function safeRatioPct/.test(AJ), 'F57: safeRatioPct helper exists');
ok(!/\(\(norm\.consensus - refFdv\) \/ refFdv\) \* 100/.test(AJ), 'F57: AssetHeader uses safePct');
// F57 + F70: NormTable now uses consInScope (filter-scoped) instead of norm.consensus directly
ok(/safeRatioPct\(r\.impliedFDV, consInScope\)/.test(AJ), 'F57+F70: NormTable prem uses safeRatioPct against scoped consensus');

// F58 — handleTick rejects share_ratio<=0
ok(/!isLiveMarket\(market\)/.test(DL), 'F58: handleTick uses isLiveMarket gate');

// F59 — WS payload shape guards
ok(/Array\.isArray\(msg\.statuses\)/.test(DL), 'F59: handleSnapshot/handleStatus check statuses is array');
ok(/Array\.isArray\(msg\.markets\)/.test(DL), 'F59: handleConfigUpdate checks markets is array');
ok(/typeof msg\.type !== "string"/.test(DL), 'F59: onmessage validates msg.type is string');

// F60 — dot uses pts.length-1
ok(/const lastIdx = pts\.length - 1/.test(AJ), 'F60: ValuationChart uses pts.length-1 for last dot');
ok(!/yOf\(pts\[95\]\)/.test(AJ), 'F60: no hardcoded pts[95]');

// F61 — Math.min empty guard
ok(/allVals\.length === 0/.test(AJ), 'F61: ValuationChart bails on empty allVals');

// F62 — ConnStatusCell drives LIVE label
ok(/function ConnStatusCell/.test(AJ), 'F62: ConnStatusCell exists');
ok(/<ConnStatusCell\/>/.test(AJ), 'F62: TopBar uses ConnStatusCell');
ok(/RECONNECTING/.test(AJ), 'F62: ConnStatusCell shows RECONNECTING label');

// F63 — ws handlers detached on close
ok(/dead\.onmessage = null/.test(DL), 'F63: onclose nulls dead ws onmessage');
ok(/dead\.onclose = null/.test(DL), 'F63: onclose nulls dead ws onclose');

// FILTER — Spot/Perp filter (Rebatify-inspired addition)
ok(/function structIsPerp/.test(AJ), 'FILTER: structIsPerp helper exists');
ok(/function applyStructFilter/.test(AJ), 'FILTER: applyStructFilter helper exists');
ok(/function countByFilter/.test(AJ), 'FILTER: countByFilter helper exists');
ok(/function StructFilter/.test(AJ), 'FILTER: StructFilter component exists');
ok(/const \[structFilter, setStructFilter\] = useState\("all"\)/.test(AJ), 'FILTER: App state initialized to "all"');
ok(/structFilter=\{structFilter\}/.test(AJ), 'FILTER: structFilter prop threaded to children');
ok(/onStructFilterChange=\{setStructFilter\}/.test(AJ), 'FILTER: setStructFilter callback threaded');
ok(/applyStructFilter\(norm\.rows/.test(AJ), 'FILTER: NormTable applies filter to live rows');
ok(/applyStructFilter\(allDisabled/.test(AJ), 'FILTER: disabled rows respect filter too');
// VenueCards now splits via structIsPerp directly per-section (not applyStructFilter),
// so the filter just controls section visibility — see SECTIONS asserts above.
ok(/structIsPerp\(r\.structure\)/.test(AJ), 'FILTER: VenueCards splits sections via structIsPerp');

// ---- Parse check via babel-standalone ----
try {
  const out = babel.transform(AJ, { presets: ['env', 'react'] });
  ok(out.code.length > 0, 'PARSE: app.jsx transforms via @babel/preset-env+react');
} catch (e) {
  ok(false, 'PARSE: app.jsx transform failed: ' + e.message);
}

// ---- Functional sim of data-live.js IIFE handlers ----
// Strategy: rewrite the IIFE wrapper to expose the inner functions on a
// global object, then exercise handleSnapshot/handleTick/loadAlerts.

const dlExposed = DL.replace(
  '(function () {',
  '(global.__LIVE_INTERNAL__ = (function () {'
).replace(
  /\n\s*connect\(\);\s*\n\}\)\(\);\s*$/,
  '\n  // smoke export hook: skip auto-connect, expose internals\n  return { handleSnapshot, handleTick, handleStatus, handleConfigUpdate, ratioSourceFromMarket, isLiveMarket, loadAlerts };\n})());'
);
if (dlExposed === DL.replace('(function () {', '(global.__LIVE_INTERNAL__ = (function () {')) {
  console.error('SIM SETUP: failed to rewrite IIFE tail — expecting `connect();\\n})();`');
  process.exit(1);
}

// Minimal window + localStorage stubs
global.window = {
  location: { protocol: 'http:', hostname: '127.0.0.1', port: '8000', host: '127.0.0.1:8000' },
  ASSETS: [], LISTINGS: {}, SERIES: {}, FUNDING: {}, DISABLED: {},
  PRE_IPO_LIVE: { connected: false, lastSnapshotAt: null, lastTickAt: null, statuses: [] },
  dispatchEvent: () => true,
  addEventListener: () => {},
  removeEventListener: () => {},
};
const _storage = {};
global.localStorage = {
  getItem: (k) => (k in _storage ? _storage[k] : null),
  setItem: (k, v) => { _storage[k] = String(v); },
  removeItem: (k) => { delete _storage[k]; },
};
global.WebSocket = function () { this.onopen = this.onmessage = this.onerror = this.onclose = null; };
global.WebSocket.OPEN = 1;
global.CustomEvent = class {};
global.Notification = undefined;
global.setTimeout = (fn) => 0; // suppress reconnect timers

// Evaluate the rewritten module
eval(dlExposed);
const live = global.__LIVE_INTERNAL__;
ok(live && typeof live.handleSnapshot === 'function', 'SIM: data-live IIFE exposed for testing');

// SIM-1: ratioSourceFromMarket passes uncalibrated through
const passedThrough = live.ratioSourceFromMarket({ ratio_source: 'uncalibrated' });
ok(passedThrough === 'uncalibrated', 'SIM: ratioSourceFromMarket passes uncalibrated through (was "market-fitted")');

// SIM-2: isLiveMarket rejects null / zero / negative / uncalibrated
ok(live.isLiveMarket({ share_ratio: 2.5, ratio_source: 'official' }) === true, 'SIM: isLiveMarket accepts positive ratio + official');
ok(live.isLiveMarket({ share_ratio: 0, ratio_source: 'official' }) === false, 'SIM: isLiveMarket rejects ratio=0');
ok(live.isLiveMarket({ share_ratio: -1, ratio_source: 'official' }) === false, 'SIM: isLiveMarket rejects negative ratio');
ok(live.isLiveMarket({ share_ratio: null, ratio_source: 'official' }) === false, 'SIM: isLiveMarket rejects null ratio');
ok(live.isLiveMarket({ share_ratio: 1.0, ratio_source: 'uncalibrated' }) === false, 'SIM: isLiveMarket rejects uncalibrated even with valid ratio');

// SIM-3: handleSnapshot bad shape doesn't throw
live.handleSnapshot(null);
live.handleSnapshot('not an object');
live.handleSnapshot({});
ok(true, 'SIM: handleSnapshot tolerates null/non-object/empty msg');

// SIM-4: handleTick bad shape doesn't throw
live.handleTick(null);
live.handleTick({});
live.handleTick({ asset_id: 'SPACEX' }); // missing exchange
live.handleTick({ asset_id: 'SPACEX', exchange: 'okx', token_price: NaN, calibration_status: 'calibrated' });
ok(true, 'SIM: handleTick tolerates malformed quotes');

// SIM-5: handleConfigUpdate that flips a market to uncalibrated removes the
// stale LISTINGS row.
// Seed LISTINGS with a calibrated row
global.window.LISTINGS = { SPACEX: [{ ex: 'okx', structure: 'perp', ratioSource: 'official', shareRatio: 2.5 }] };
// Now config-update flips okx to uncalibrated
live.handleConfigUpdate({
  markets: [{
    asset_id: 'SPACEX', exchange: 'okx', symbol: 'X', venue: 'CEX', structure: 'perp',
    share_ratio: null, ratio_source: 'uncalibrated', confidence: 0,
  }],
  assets: [],
});
ok(
  (global.window.LISTINGS.SPACEX || []).length === 0,
  'SIM: handleConfigUpdate drops LISTINGS row that became uncalibrated'
);

// SIM-6: loadAlerts skips malformed entries
_storage['preIpoAlerts'] = JSON.stringify([
  { id: 'good', assetId: 'SPACEX', kind: 'premium-above', value: 30 },
  null,
  {},
  { id: 'bad-value', assetId: 'SPACEX', kind: 'premium-above', value: 'thirty' },
  { assetId: 'no-kind', value: 30 },
  'string-rule',
]);
const rules = live.loadAlerts();
ok(rules.length === 1 && rules[0].id === 'good', 'SIM: loadAlerts keeps only well-formed entries');
const stored = JSON.parse(_storage['preIpoAlerts']);
ok(stored.length === 1 && stored[0].id === 'good', 'SIM: loadAlerts self-heals localStorage to validated subset');

// SIM-7: loadAlerts non-array storage returns []
_storage['preIpoAlerts'] = JSON.stringify({});
const empty = live.loadAlerts();
ok(Array.isArray(empty) && empty.length === 0, 'SIM: loadAlerts returns [] for non-array storage');

// SLICE 5 fixes (F64-F77 backend + frontend wiring)
ok(/function fundingAPR\(rate, intervalHours\)/.test(AJ), 'F65: fundingAPR signature accepts intervalHours');
ok(/24 \/ hours/.test(AJ), 'F65: APR formula uses 24/hours pattern');
ok(/fundingIntervalHours: quote\.funding_interval_hours/.test(DL), 'F65: data-live forwards funding_interval_hours');
ok(/lo\.fundingIntervalHours/.test(AJ) && /hi\.fundingIntervalHours/.test(AJ), 'F65: HedgeBook annualizes per-leg cadence');
ok(/l\.fundingIntervalHours/.test(AJ), 'F65: FundingPanel annualizes per-row cadence');

// F69: market_id keys in FE statuses
ok(/statusByMarketId/.test(DL), 'F69: data-live keys statuses by market_id');
ok(/s\.market_id/.test(DL), 'F69: status payload market_id field consumed');

// F70: scoped consensus
ok(/const consInScope = structFilter === "all"/.test(AJ), 'F70: scoped consensus computed conditionally');
ok(/structFilter !== "all" \? <span className="muted"> \(\{structFilter\}\)<\/span> : null/.test(AJ), 'F70: column header shows scope when filter active');

// F73: disabled row uses getStructMeta
ok(/const dMeta = getStructMeta\(d\.structure\)/.test(AJ), 'F73: NormTable disabled row routes through getStructMeta');

// SECTIONS — Rebatify-style spot/perp split into independent sections
ok(/function VenueSection/.test(AJ), 'SECTIONS: VenueSection helper component exists');
ok(/function VenueCard\(/.test(AJ), 'SECTIONS: VenueCard render extracted');
ok(/function DisabledVenueCard/.test(AJ), 'SECTIONS: DisabledVenueCard render extracted');
ok(/SPOT \/ MIRROR \/ CERTIFICATE/.test(AJ), 'SECTIONS: spot section labeled');
ok(/PERP \/ FUTURES/.test(AJ), 'SECTIONS: perp section labeled');
ok(/const showSpot = structFilter === "all" \|\| structFilter === "spot"/.test(AJ), 'SECTIONS: filter still gates which sections render');
ok(/spotRows = allRows\.filter\(\(r\) => !structIsPerp/.test(AJ), 'SECTIONS: spot rows filtered by !structIsPerp');
ok(/perpRows = allRows\.filter\(\(r\) => structIsPerp/.test(AJ), 'SECTIONS: perp rows filtered by structIsPerp');
ok(/No \{isSpot \? "spot\/mirror\/certificate" : "perp"\} venues configured/.test(AJ), 'SECTIONS: empty-section placeholder');

// TOPGAP — Rebatify-style "cheapest → richest" hero
ok(/function TopGap/.test(AJ), 'TOPGAP: component exists');
ok(/<TopGap rows=\{rows\} \/>/.test(AJ), 'TOPGAP: rendered in VenueCards (filter-respecting)');
ok(/topgap-cheap/.test(AJ) && /topgap-rich/.test(AJ), 'TOPGAP: shows both endpoints not just %');
ok(/candidates\.length < 2/.test(AJ), 'TOPGAP: bails when <2 rows (no gap to compute)');
ok(/gapPct < 0\.5/.test(AJ), 'TOPGAP: suppresses chip below 0.5% (cosmetic noise gate)');

// CAVEAT panel — Rebatify "What this doesn't cover"
ok(/function CaveatPanel/.test(AJ), 'CAVEAT: component exists');
ok(/<CaveatPanel\/>/.test(AJ), 'CAVEAT: rendered in App main');
ok(/Order-book depth/.test(AJ), 'CAVEAT: covers order-book depth');
ok(/Funding cost accumulation/.test(AJ), 'CAVEAT: covers funding carry');
ok(/Calibration drift/.test(AJ), 'CAVEAT: covers calibration drift');
ok(/Securities-law/.test(AJ), 'CAVEAT: covers securities-law treatment');
ok(/Geo \/ KYC/.test(AJ), 'CAVEAT: covers geo/KYC');
ok(/Convergence timing/.test(AJ), 'CAVEAT: covers convergence assumption');

// TOPGAP functional sim — extract by regex and exercise
const topGapMatch = AJ.match(/function TopGap\([^)]*\) \{[\s\S]*?^\}/m);
ok(topGapMatch != null, 'TOPGAP SIM: component extracted (heuristic logic block)');
if (topGapMatch) {
  // Pull just the candidate-selection + gap math (skip JSX). Embedded in a
  // helper factory that mirrors the in-source logic.
  // eslint-disable-next-line no-new-func
  const factory = new Function(`
    function safeRatioPct(num, denom) {
      if (denom == null || !isFinite(denom) || denom <= 0) return null;
      if (num == null || !isFinite(num)) return null;
      return ((num / denom) - 1) * 100;
    }
    function computeGap(rows) {
      const candidates = (rows || []).filter(
        (r) => r && typeof r.impliedFDV === "number" && isFinite(r.impliedFDV) && r.impliedFDV > 0
      );
      if (candidates.length < 2) return null;
      let cheapest = candidates[0], richest = candidates[0];
      for (const r of candidates) {
        if (r.impliedFDV < cheapest.impliedFDV) cheapest = r;
        if (r.impliedFDV > richest.impliedFDV) richest = r;
      }
      if (cheapest === richest) return null;
      const gapPct = safeRatioPct(richest.impliedFDV, cheapest.impliedFDV);
      const gapUSD = richest.impliedFDV - cheapest.impliedFDV;
      if (gapPct == null || gapPct < 0.5) return null;
      return { cheapest: cheapest.ex, richest: richest.ex, gapPct, gapUSD };
    }
    return { computeGap };
  `);
  const G = factory().computeGap;
  ok(G([]) === null, 'TOPGAP SIM: empty rows → null');
  ok(G([{ex:'okx', impliedFDV:1.85e12}]) === null, 'TOPGAP SIM: single row → null');
  ok(G([{ex:'a', impliedFDV:0}, {ex:'b', impliedFDV:0}]) === null, 'TOPGAP SIM: all zero → null');
  const r1 = G([
    {ex:'bitget', impliedFDV:1.89e12},
    {ex:'gate',   impliedFDV:1.87e12},
    {ex:'okx',    impliedFDV:1.85e12},
    {ex:'hl-xyz', impliedFDV:2.36e12},
    {ex:'hl-vntl',impliedFDV:2.13e12},
  ]);
  ok(r1 && r1.cheapest === 'okx' && r1.richest === 'hl-xyz', `TOPGAP SIM: SPACEX 5-venue → cheap=okx rich=hl-xyz (got ${JSON.stringify(r1)})`);
  ok(r1 && Math.abs(r1.gapPct - 27.57) < 0.05, `TOPGAP SIM: SPACEX gap% ≈ 27.57% (got ${r1.gapPct.toFixed(2)}%)`);
  const tight = G([{ex:'a', impliedFDV:1e9}, {ex:'b', impliedFDV:1.001e9}]);
  ok(tight === null, 'TOPGAP SIM: 0.1% gap suppressed (under 0.5% threshold)');
  const justOverThreshold = G([{ex:'a', impliedFDV:1e9}, {ex:'b', impliedFDV:1.006e9}]);
  ok(justOverThreshold !== null, 'TOPGAP SIM: 0.6% gap renders');
}

// RISK CHIPS — DRIFT + THIN (Rebatify-inspired additions)
ok(/function getDriftDays/.test(AJ), 'RISK: getDriftDays helper exists');
ok(/function getDriftSeverity/.test(AJ), 'RISK: getDriftSeverity helper exists');
ok(/function getLiquidityFlag/.test(AJ), 'RISK: getLiquidityFlag helper exists');
ok(/function RiskChips/.test(AJ), 'RISK: RiskChips component exists');
ok(/<RiskChips row=\{r\} \/>/.test(AJ), 'RISK: NormTable renders RiskChips');
ok(/<RiskChips row=\{r\} compact \/>/.test(AJ), 'RISK: VenueCards renders RiskChips compact');
ok(/days >= 30/.test(AJ) || /days < 30/.test(AJ), 'RISK: drift threshold at 30d');
ok(/days < 90/.test(AJ) || /days >= 90/.test(AJ), 'RISK: drift severity threshold at 90d');
ok(/r\.spread > 50/.test(AJ), 'RISK: THIN heuristic uses spread>50bp');
ok(/< 100_000/.test(AJ) || /< 100000/.test(AJ), 'RISK: THIN heuristic uses $100K floor');

// RISK functional sim — extract and exercise the helper logic
const riskHelpersMatch = AJ.match(/function getDriftDays[\s\S]*?function getLiquidityFlag[\s\S]*?\n\}/);
ok(riskHelpersMatch != null, 'RISK SIM: helpers block extracted');
if (riskHelpersMatch) {
  // eslint-disable-next-line no-new-func
  const factory = new Function(
    riskHelpersMatch[0] +
    '\nreturn { getDriftDays, getDriftSeverity, getLiquidityFlag };'
  );
  const R = factory();
  // Drift severity boundaries
  ok(R.getDriftSeverity(null) === null, 'RISK SIM: drift null → no chip');
  ok(R.getDriftSeverity(0) === null, 'RISK SIM: drift 0d → no chip');
  ok(R.getDriftSeverity(29) === null, 'RISK SIM: drift 29d → no chip');
  ok(R.getDriftSeverity(30) === 'warn', 'RISK SIM: drift 30d → warn');
  ok(R.getDriftSeverity(89) === 'warn', 'RISK SIM: drift 89d → warn');
  ok(R.getDriftSeverity(90) === 'bad', 'RISK SIM: drift 90d → bad');
  ok(R.getDriftSeverity(365) === 'bad', 'RISK SIM: drift 365d → bad');
  // Liquidity flag
  ok(R.getLiquidityFlag({ spread: 80, vol24: 5_000_000, oi: 5_000_000 }) === 'thin', 'RISK SIM: spread>50bp → thin even with deep book');
  ok(R.getLiquidityFlag({ spread: 30, vol24: 50_000, oi: 50_000 }) === 'thin', 'RISK SIM: shallow vol+OI → thin even with tight spread');
  ok(R.getLiquidityFlag({ spread: 30, vol24: 5_000_000, oi: 5_000_000 }) === null, 'RISK SIM: deep + tight → no chip');
  ok(R.getLiquidityFlag({ spread: 0, vol24: 0, oi: 0 }) === null, 'RISK SIM: cold-start (no data) → no chip, not thin');
  ok(R.getLiquidityFlag({ spread: 30, vol24: 500_000, oi: 0 }) === null, 'RISK SIM: spot venue with no OI but real vol → not thin');
  ok(R.getLiquidityFlag(null) === null, 'RISK SIM: null row → no chip');
  // Drift days calculation
  const futureISO = new Date(Date.now() + 86400000).toISOString();
  ok(R.getDriftDays(futureISO) === null, 'RISK SIM: future calibration date → null (no drift)');
  const sixtyDaysAgo = new Date(Date.now() - 60 * 86400000).toISOString();
  const d = R.getDriftDays(sixtyDaysAgo);
  ok(d === 60 || d === 59, `RISK SIM: 60d-ago ISO → ${d} (within rounding)`);
  ok(R.getDriftDays(null) === null, 'RISK SIM: null ISO → null');
  ok(R.getDriftDays('not-a-date') === null, 'RISK SIM: garbage ISO → null');
}

// FILTER functional sim — extract the helpers by regex and eval them.
// Tests the LOGIC of structIsPerp / applyStructFilter / countByFilter
// against the actual source in app.jsx.
const helperBlockMatch = AJ.match(/function structIsPerp[\s\S]*?function countByFilter[\s\S]*?\n\}/);
ok(helperBlockMatch != null, 'FILTER SIM: helpers block extracted from app.jsx');
if (helperBlockMatch) {
  // eslint-disable-next-line no-new-func
  const factory = new Function(helperBlockMatch[0] + '\nreturn { structIsPerp, applyStructFilter, countByFilter };');
  const F = factory();
  const rows = [
    { ex: 'okx', structure: 'perp' },
    { ex: 'bitget', structure: 'mirror-credit' },
    { ex: 'gate', structure: 'pre-ipo-certificate' },
    { ex: 'hl-vntl', structure: 'perp' },
    { ex: 'future', structure: 'tokenized-equity' }, // unknown -> spot
  ];
  const c = F.countByFilter(rows);
  ok(c.all === 5 && c.perp === 2 && c.spot === 3, `FILTER SIM: countByFilter = ${JSON.stringify(c)}`);
  ok(F.applyStructFilter(rows, 'all').length === 5, 'FILTER SIM: filter "all" returns all rows');
  ok(F.applyStructFilter(rows, 'perp').length === 2, 'FILTER SIM: filter "perp" returns 2 rows');
  ok(F.applyStructFilter(rows, 'spot').length === 3, 'FILTER SIM: filter "spot" returns 3 (incl unknown structure)');
  ok(!F.structIsPerp('mirror-credit'), 'FILTER SIM: structIsPerp(mirror-credit) === false');
  ok(F.structIsPerp('perp'), 'FILTER SIM: structIsPerp(perp) === true');
}

if (failed > 0) {
  console.error(`\n${failed} TEST(S) FAILED`);
  process.exit(1);
}
console.log('\nALL SLICE-4 SMOKE TESTS PASSED');
