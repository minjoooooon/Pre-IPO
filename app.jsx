/* global React, ReactDOM */
const { useState, useMemo, useEffect, useRef } = React;

// ============================================================
// formatting helpers
// ============================================================
const fmtUSD = (n) => {
  if (n == null || !isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n/1e12).toFixed(2) + "T";
  if (abs >= 1e9)  return (n/1e9).toFixed(2) + "B";
  if (abs >= 1e6)  return (n/1e6).toFixed(1) + "M";
  if (abs >= 1e3)  return (n/1e3).toFixed(1) + "K";
  return Number(n).toFixed(2);
};

// Split a USD value into {num, unit} so the hero display can keep its
// separate <span> structure while the unit auto-scales (B ↔ T).
const splitUSDUnit = (n) => {
  if (n == null || !isFinite(n)) return { num: "—", unit: "" };
  const abs = Math.abs(n);
  if (abs >= 1e12) return { num: (n/1e12).toFixed(2), unit: "T" };
  if (abs >= 1e9)  return { num: (n/1e9).toFixed(2),  unit: "B" };
  if (abs >= 1e6)  return { num: (n/1e6).toFixed(1),  unit: "M" };
  if (abs >= 1e3)  return { num: (n/1e3).toFixed(1),  unit: "K" };
  return { num: Number(n).toFixed(2), unit: "" };
};
const fmtNum = (n, d=2) => Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (n, d=2) => (n>=0?"+":"") + n.toFixed(d) + "%";

// Premium display: small % stays as %, huge ones switch to multiplier.
// e.g. +85.4% → "+85.4%"  but  +2210.9% → "+23.11x"
const fmtPremium = (pct, d=2) => {
  if (pct == null || !isFinite(pct)) return "—";
  const abs = Math.abs(pct);
  if (abs >= 500) {
    const mult = 1 + pct / 100;
    return (mult >= 0 ? "+" : "") + mult.toFixed(d) + "x";
  }
  return (pct >= 0 ? "+" : "") + pct.toFixed(d) + "%";
};
const fmtTime = (d) => d.toISOString().slice(11,19);
const fmtTimeKST = (d) => {
  // explicit Asia/Seoul timezone for Korean user (CLAUDE.md currentDate context)
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false, timeZone: "Asia/Seoul"
  }).format(d);
};

// ============================================================
// TickAge — tiny age badge so users can spot stale feeds at a glance
// ============================================================
function TickAge({ tickAt }) {
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force(x => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  if (!tickAt) return null;
  const age = Math.floor((Date.now() - tickAt) / 1000);
  let cls = "tick-age";
  if (age > 60) cls += " bad";
  else if (age > 15) cls += " warn";
  else cls += " ok";
  const display = age < 10 ? `${age}s` : age < 60 ? `${age}s` : age < 3600 ? `${Math.floor(age/60)}m` : `${Math.floor(age/3600)}h`;
  return <span className={cls} title={`last tick ${age}s ago`}>·{display}</span>;
}

// ============================================================
// structure / source visual mapping
// ============================================================
const STRUCT_META = {
  "SPV-backed":         { sym: "S", tone: "good",    label: "SPV-backed" },
  "SPV-mirror":         { sym: "S", tone: "good",    label: "SPV-mirror" },
  "synthetic":          { sym: "Y", tone: "neutral", label: "Synthetic" },
  "mirror-credit":      { sym: "M", tone: "neutral", label: "Mirror credit" },
  "perp":               { sym: "P", tone: "warn",    label: "Perp" },
  "pre-ipo-certificate":{ sym: "C", tone: "neutral", label: "Pre-IPO certificate" }
};
const SRC_META = {
  "official":      { dot: "good",    label: "Official" },
  "whitepaper":    { dot: "neutral", label: "Whitepaper" },
  "back-solved":   { dot: "warn",    label: "Back-solved" },
  "market-fitted": { dot: "bad",     label: "Market-fitted" }
};

// ============================================================
// funding helpers — REAL data from backend.
// Each listing row carries the latest funding_rate emitted by the exchange
// (OKX funding-rate channel). window.FUNDING[asset][exchange] holds a
// bounded de-duped history for sparklines. Spot/mirror markets return null.
// ============================================================
function getFundingRate(listingRow) {
  if (!listingRow) return null;
  return listingRow.fundingRate ?? null;
}
function getFundingHistory(assetId, ex) {
  const f = (typeof window !== "undefined" && window.FUNDING) || {};
  return (f[assetId] && f[assetId][ex]) || null;
}
function fundingAPR(rate) {
  // OKX funding settles every 8h → 3 settlements/day → 1095/yr
  return rate * 3 * 365;
}

// ============================================================
// hedge combinatorics — long cheapest, short richest
// ============================================================
function buildHedgeBook(rows, assetId) {
  const combos = [];
  for (let i=0; i<rows.length; i++) {
    for (let j=0; j<rows.length; j++) {
      if (i===j) continue;
      const lo = rows[i], hi = rows[j];
      if (lo.impliedFDV >= hi.impliedFDV) continue;
      const gapPct = (hi.impliedFDV / lo.impliedFDV - 1) * 100;
      const gapUSD = hi.impliedFDV - lo.impliedFDV;
      const loFund = lo.structure === "perp" ? getFundingRate(lo) : null;
      const hiFund = hi.structure === "perp" ? getFundingRate(hi) : null;
      // long pays positive funding on its leg, short receives positive funding on its leg
      const longCarryApr  = loFund == null ? 0 : -fundingAPR(loFund) * 100;
      const shortCarryApr = hiFund == null ? 0 :  fundingAPR(hiFund) * 100;
      const netFundingApr = longCarryApr + shortCarryApr;
      const minConf = Math.min(lo.confidence, hi.confidence);
      // structure-mismatch flag
      const longSPV  = lo.structure.startsWith("SPV");
      const shortSPV = hi.structure.startsWith("SPV");
      const mismatch = longSPV !== shortSPV;
      // capital efficiency: avg spread bp
      const execBp = (lo.spread + hi.spread) / 2;
      combos.push({
        long: lo, short: hi,
        gapPct, gapUSD,
        loFund, hiFund,
        longCarryApr, shortCarryApr, netFundingApr,
        minConf, mismatch, execBp
      });
    }
  }
  combos.sort((a,b)=>b.gapPct - a.gapPct);
  return combos;
}

// ============================================================
// normalization core
// ============================================================
function normalize(listings, fdvShares) {
  const rows = listings.map(l => {
    const impliedShare = l.tokenPrice / l.shareRatio;
    const impliedFDV   = impliedShare * fdvShares;
    return { ...l, impliedShare, impliedFDV };
  });
  // weighted consensus by confidence × sqrt(size), where size = oi when
  // available (perp/futures) else vol24 (spot/mirror-credit). Both are
  // USD-denominated by the adapters so the unit is consistent.
  let wsum = 0, vsum = 0;
  rows.forEach(r => {
    const size = (r.oi && r.oi > 0) ? r.oi : (r.vol24 || 0);
    const w = (r.confidence / 100) * Math.sqrt(Math.max(size, 1));
    wsum += w; vsum += w * r.impliedFDV;
  });
  const consensus = wsum > 0 ? vsum / wsum : 0;
  const max = rows.reduce((a,b)=>b.impliedFDV>a.impliedFDV?b:a, rows[0]);
  const min = rows.reduce((a,b)=>b.impliedFDV<a.impliedFDV?b:a, rows[0]);
  const avgConf = rows.reduce((s,r)=>s+r.confidence,0) / rows.length;
  return { rows, consensus, max, min, avgConf, spread: (max.impliedFDV - min.impliedFDV) / consensus * 100 };
}

// ============================================================
// header / top bar
// ============================================================
function TopBar({ now, asset }) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="logo">
          <span className="logo-mark">◆</span>
          <span className="logo-text">PRE/IPO<span className="logo-sub">·NORMALIZER</span></span>
        </div>
        <nav className="top-nav">
          <a className="top-nav-item active">VALUATION</a>
          <a className="top-nav-item disabled" title="not implemented">ORDER FLOW <span className="muted">·soon</span></a>
          <a className="top-nav-item disabled" title="not implemented">SPREADS <span className="muted">·soon</span></a>
          <a className="top-nav-item disabled" title="not implemented">NEWS <span className="muted">·soon</span></a>
          <a className="top-nav-item disabled" title="not implemented">ALERTS <span className="muted">·soon</span></a>
        </nav>
      </div>
      <div className="topbar-right">
        <div className="status-cell">
          <span className="status-dot pulse" />
          <span className="status-label">LIVE</span>
        </div>
        <div className="status-cell mono">
          <span className="muted">UTC</span>
          <span>{fmtTime(now)}</span>
        </div>
        <div className="status-cell mono">
          <span className="muted">KST</span>
          <span>{fmtTimeKST(now)}</span>
        </div>
        <div className="status-cell mono">
          <span className="muted">SESSION</span>
          <span className="ok">OPEN</span>
        </div>
        <FeedsCell />
        <button className="cmd-btn">
          <span className="kbd">⌘K</span>
          <span>SEARCH INSTRUMENT…</span>
        </button>
      </div>
    </header>
  );
}

function FeedsCell() {
  const live = (typeof window !== "undefined" && window.PRE_IPO_LIVE) || { statuses: [], connected: false };
  const statuses = live.statuses || [];
  const total = statuses.length || 0;
  const connected = statuses.filter(s => s.state === "connected").length;
  const cls = !live.connected ? "bad" : connected === 0 ? "bad" : connected < total ? "warn" : "ok";
  return (
    <div className="status-cell mono">
      <span className="muted">FEEDS</span>
      <span className={cls}>{connected}/{total || "-"}</span>
    </div>
  );
}

// ============================================================
// asset rail
// ============================================================
function AssetRail({ assets, selectedId, onSelect, normMap }) {
  return (
    <aside className="rail">
      <div className="rail-section-h">
        <span>WATCHLIST</span>
        <span className="muted">{assets.length}</span>
      </div>
      <div className="rail-list">
        {assets.map(a => {
          const n = normMap[a.id];
          const drift = ((n.consensus - a.lastRound.valuation) / a.lastRound.valuation) * 100;
          const up = a.delta24h >= 0;
          return (
            <button
              key={a.id}
              className={"rail-row" + (a.id === selectedId ? " active" : "")}
              onClick={() => onSelect(a.id)}
            >
              <div className="rail-row-top">
                <span className="rail-ticker">{a.ticker}</span>
                <span className={"rail-delta " + (up ? "ok" : "bad")}>{fmtPct(a.delta24h)}</span>
              </div>
              <div className="rail-row-mid">
                <span className="rail-name">{a.name}</span>
              </div>
              <div className="rail-row-bot">
                <span className="mono">${fmtUSD(n.consensus)}</span>
                <span
                  className={"mono " + (drift>=0?"ok":"bad")}
                  title={`Implied FDV ${fmtPremium(drift)} vs $${fmtUSD(a.lastRound.valuation)} round (${a.lastRound.date})`}
                >
                  vs ref {fmtPremium(drift)}
                </span>
              </div>
              <Sparkline points={a._spark} positive={up} />
            </button>
          );
        })}
      </div>

      <div className="rail-section-h" style={{marginTop:18}}>
        <span>STRUCTURE LEGEND</span>
      </div>
      <div className="legend">
        {Object.entries(STRUCT_META).map(([k,v])=>(
          <div key={k} className="legend-row">
            <span className={"struct-pill tone-"+v.tone}>{v.sym}</span>
            <span className="legend-label">{v.label}</span>
          </div>
        ))}
      </div>
      <div className="rail-foot">
        <div className="thesis">
          <div className="thesis-h">THESIS</div>
          <div className="thesis-body">
            This dashboard does <em>not</em> compare token prices directly.
            It normalizes synthetic pre-market instruments into <span className="hl">implied company valuations</span>.
          </div>
        </div>
      </div>
    </aside>
  );
}

function Sparkline({ points, positive }) {
  if (!points) return null;
  const w = 200, h = 22;
  const min = Math.min(...points), max = Math.max(...points);
  const rng = max - min || 1;
  const path = points.map((p,i) => `${i===0?"M":"L"}${(i/(points.length-1))*w},${h - ((p-min)/rng)*h}`).join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={path} className={positive ? "spark-up" : "spark-down"} fill="none" />
    </svg>
  );
}

// ============================================================
// asset header strip
// ============================================================
function AssetHeader({ asset, norm }) {
  const refFdv = asset.lastRound.valuation;
  const driftPct = ((norm.consensus - refFdv) / refFdv) * 100;
  const gapUSD = norm.consensus - refFdv;
  const up = asset.delta24h >= 0;
  const premUp = driftPct >= 0;
  const refUnit = splitUSDUnit(refFdv);
  const gapUnit = splitUSDUnit(Math.abs(gapUSD));
  return (
    <section className="asset-head">
      <div className="asset-head-left">
        <div className="asset-ticker-row">
          <span className="asset-ticker">{asset.ticker}</span>
          <span className="asset-name">{asset.name}</span>
          <span className="tag tag-sector">{asset.sector}</span>
          <span className="tag tag-priv">PRIVATE · PRE-IPO</span>
        </div>
        <div className="asset-headline">
          <span className="hl-dot" /> {asset.headline}
        </div>
      </div>
      <div className="asset-head-right">
        <div className="hero-fdv">
          <div className="hero-label">
            CONSENSUS IMPLIED FDV
            <AlertButton assetId={asset.id} />
          </div>
          <div className="hero-value mono">
            <span className="hero-currency">$</span>
            <span className="hero-num">{splitUSDUnit(norm.consensus).num}</span>
            <span className="hero-unit">{splitUSDUnit(norm.consensus).unit}</span>
            <span className={"hero-prem mono " + (premUp ? "ok" : "bad")}>
              {fmtPremium(driftPct)}
            </span>
          </div>
          <div className="hero-sub">
            <span className="muted">vs ref</span>
            <span className="mono">${refUnit.num}{refUnit.unit}</span>
            <span className="muted">({asset.lastRound.date})</span>
            <span className="sep">·</span>
            <span className={"mono " + (premUp ? "ok" : "bad")}>
              gap {premUp ? "+" : "−"}${gapUnit.num}{gapUnit.unit}
            </span>
            <span className="sep">·</span>
            <span className={up ? "ok" : "bad"}>{fmtPct(asset.delta24h)} <span className="muted">24h</span></span>
          </div>
        </div>
      </div>
    </section>
  );
}

// ============================================================
// KPI strip
// ============================================================
function KPIStrip({ asset, norm }) {
  const refFdv = asset.lastRound.valuation;
  const consVsRef = ((norm.consensus - refFdv) / refFdv) * 100;
  const items = [
    {
      label: "Consensus FDV",
      sub: "vol·conf weighted",
      value: "$" + fmtUSD(norm.consensus),
      foot: (
        <span className={consVsRef >= 0 ? "ok" : "bad"}>
          {fmtPremium(consVsRef)} <span className="muted">vs ref</span>
        </span>
      )
    },
    {
      label: "High venue",
      sub: norm.max.ex,
      value: "$" + fmtUSD(norm.max.impliedFDV),
      foot: <span className="ok">+{((norm.max.impliedFDV/norm.consensus-1)*100).toFixed(2)}% vs cons.</span>
    },
    {
      label: "Low venue",
      sub: norm.min.ex,
      value: "$" + fmtUSD(norm.min.impliedFDV),
      foot: <span className="bad">{((norm.min.impliedFDV/norm.consensus-1)*100).toFixed(2)}% vs cons.</span>
    },
    {
      label: "Spread (hi–lo)",
      sub: "normalized FDV",
      value: norm.spread.toFixed(2) + "%",
      foot: <span className="muted">{fmtUSD(norm.max.impliedFDV-norm.min.impliedFDV)} gap</span>
    },
    {
      label: "Avg confidence",
      sub: norm.rows.length + " venues",
      value: norm.avgConf.toFixed(0) + "/100",
      foot: <ConfBar value={norm.avgConf} />
    },
    {
      label: "FDV shares",
      sub: "editable",
      value: (asset.fdvShares/1e9).toFixed(2) + "B",
      foot: <span className="muted">last round ${fmtUSD(asset.lastRound.valuation)}</span>
    }
  ];
  return (
    <div className="kpi-strip">
      {items.map((k,i)=>(
        <div key={i} className="kpi-cell">
          <div className="kpi-label">
            <span>{k.label}</span>
            <span className="kpi-sub">{k.sub}</span>
          </div>
          <div className="kpi-value mono">{k.value}</div>
          <div className="kpi-foot mono">{k.foot}</div>
        </div>
      ))}
    </div>
  );
}

function ConfBar({ value }) {
  return (
    <div className="confbar">
      <div className="confbar-fill" style={{width: value+"%"}} />
    </div>
  );
}

// ============================================================
// multi-line chart of implied FDV per exchange
// ============================================================
function ValuationChart({ asset, listings, series, norm }) {
  const W = 880, H = 320, PAD = { l: 56, r: 12, t: 16, b: 26 };
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;

  const [hoverX, setHoverX] = useState(null);
  const ref = useRef(null);

  // collect all values for y-range
  const allVals = [];
  listings.forEach(l => { (series[l.ex] || []).forEach(v => allVals.push(v)); });
  const yMin = Math.min(...allVals) * 0.985;
  const yMax = Math.max(...allVals) * 1.015;

  const xOf = (i) => PAD.l + (i/(95)) * innerW;
  const yOf = (v) => PAD.t + innerH - ((v - yMin)/(yMax - yMin)) * innerH;

  // y ticks
  const ticks = 5;
  const tickVals = Array.from({length: ticks+1}, (_,i) => yMin + (yMax-yMin)*i/ticks);

  // x labels (every 24 points = 6h)
  const xLabels = [0, 24, 48, 72, 95];
  const labelFor = (i) => {
    const hrs = Math.round(24 - i/4);
    return hrs === 0 ? "now" : `-${hrs}h`;
  };

  // colors per venue type (route through structure)
  const colorFor = (l) => {
    if (l.structure.startsWith("SPV")) return "var(--c-cyan)";
    if (l.structure === "synthetic")    return "var(--c-amber)";
    if (l.structure === "mirror-credit")return "var(--c-violet)";
    return "var(--c-magenta)"; // perp
  };

  function onMove(e) {
    const r = ref.current.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((x - PAD.l)/innerW) * 95);
    if (i >= 0 && i <= 95) setHoverX(i); else setHoverX(null);
  }

  return (
    <div className="panel chart-panel">
      <PanelHead title="IMPLIED FDV — 24H INTRADAY" sub="Normalized to USD; each line = 1 venue's instrument" right={
        <div className="panel-tabs">
          <span className="tab active">1D</span>
          <span className="tab">7D</span>
          <span className="tab">30D</span>
          <span className="tab">ALL</span>
        </div>
      }/>
      <div className="chart-wrap">
        <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="chart" onMouseMove={onMove} onMouseLeave={()=>setHoverX(null)}>
          {/* grid */}
          {tickVals.map((v,i)=>(
            <g key={i}>
              <line x1={PAD.l} x2={W-PAD.r} y1={yOf(v)} y2={yOf(v)} className="grid-line"/>
              <text x={PAD.l-8} y={yOf(v)+3} className="axis-text" textAnchor="end">${fmtUSD(v)}</text>
            </g>
          ))}
          {xLabels.map(i=>(
            <text key={i} x={xOf(i)} y={H-8} className="axis-text" textAnchor="middle">{labelFor(i)}</text>
          ))}
          {/* consensus band */}
          <line x1={PAD.l} x2={W-PAD.r} y1={yOf(norm.consensus)} y2={yOf(norm.consensus)} className="consensus-line"/>
          <text x={W-PAD.r-6} y={yOf(norm.consensus)-6} className="axis-text consensus-text" textAnchor="end">
            CONSENSUS ${fmtUSD(norm.consensus)}
          </text>
          {/* paths */}
          {listings.map((l,i)=> {
            const pts = series[l.ex];
            if (!pts) return null;
            const d = pts.map((p,k)=>`${k===0?"M":"L"}${xOf(k)},${yOf(p)}`).join(" ");
            const c = colorFor(l);
            const opacity = l.confidence >= 70 ? 0.95 : l.confidence >= 50 ? 0.7 : 0.5;
            return (
              <g key={l.ex}>
                <path d={d} fill="none" stroke={c} strokeWidth={l.confidence>=80?1.8:1.2} opacity={opacity}/>
                <circle cx={xOf(95)} cy={yOf(pts[95])} r={3} fill={c}/>
              </g>
            );
          })}
          {/* hover */}
          {hoverX !== null && (
            <g>
              <line x1={xOf(hoverX)} x2={xOf(hoverX)} y1={PAD.t} y2={H-PAD.b} className="hover-line"/>
              {listings.map(l => {
                const v = series[l.ex] && series[l.ex][hoverX];
                if (!v) return null;
                return <circle key={l.ex} cx={xOf(hoverX)} cy={yOf(v)} r={2.5} fill={colorFor(l)}/>;
              })}
            </g>
          )}
        </svg>
        {hoverX !== null && (
          <HoverCard listings={listings} series={series} hoverX={hoverX} colorFor={colorFor}/>
        )}
      </div>
      <div className="chart-legend">
        {listings.map(l => (
          <div key={l.ex} className="legend-item">
            <span className="legend-dash" style={{background: l.structure.startsWith("SPV") ? "var(--c-cyan)" : l.structure==="synthetic" ? "var(--c-amber)" : l.structure==="mirror-credit" ? "var(--c-violet)" : "var(--c-magenta)"}}/>
            <span className="legend-text">{l.ex}</span>
            <span className="muted mono">{STRUCT_META[l.structure].label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function HoverCard({ listings, series, hoverX, colorFor }) {
  const rows = listings.map(l => ({ l, v: series[l.ex] ? series[l.ex][hoverX] : null }))
                       .filter(r => r.v != null)
                       .sort((a,b)=>b.v - a.v);
  return (
    <div className="hover-card">
      <div className="hover-card-h mono">t = -{Math.round(24 - hoverX/4)}h</div>
      {rows.map(r => (
        <div key={r.l.ex} className="hover-row">
          <span className="hover-dot" style={{background: colorFor(r.l)}}/>
          <span className="hover-ex">{r.l.ex}</span>
          <span className="mono">${fmtUSD(r.v)}</span>
        </div>
      ))}
    </div>
  );
}

// ============================================================
// premium / discount bars
// ============================================================
function PremiumBars({ norm }) {
  const ref = norm.consensus;
  const rows = [...norm.rows]
    .map(r => ({ ...r, prem: (r.impliedFDV/ref - 1) * 100 }))
    .sort((a,b)=>b.prem-a.prem);
  const max = Math.max(...rows.map(r=>Math.abs(r.prem))) * 1.2 || 1;
  return (
    <div className="panel">
      <PanelHead title="PREMIUM / DISCOUNT" sub="vs consensus implied FDV"/>
      <div className="bars">
        {rows.map(r => {
          const pct = (r.prem/max) * 50;
          return (
            <div key={r.ex} className="bar-row">
              <div className="bar-name">
                <span className={"struct-pill tone-"+STRUCT_META[r.structure].tone}>{STRUCT_META[r.structure].sym}</span>
                <span>{r.ex}</span>
              </div>
              <div className="bar-track">
                <div className="bar-center"/>
                <div className={"bar-fill " + (r.prem>=0?"pos":"neg")}
                     style={{
                       left: r.prem>=0 ? "50%" : (50+pct)+"%",
                       width: Math.abs(pct)+"%"
                     }}/>
                <div className="bar-zero" style={{left:"50%"}}/>
              </div>
              <div className={"bar-val mono " + (r.prem>=0?"ok":"bad")}>{fmtPct(r.prem,2)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// API helpers (admin write)
// ============================================================
const API_BASE = (function() {
  if (typeof window !== "undefined" && window.location && window.location.port === "8000") {
    return "";
  }
  const host = (typeof window !== "undefined" && window.location.hostname) || "127.0.0.1";
  return `http://${host}:8000`;
})();

async function postShares(assetId, shares, source, confidence) {
  const r = await fetch(`${API_BASE}/api/admin/asset/${assetId}/shares`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fully_diluted_shares: shares, source, confidence }),
  });
  if (!r.ok) throw new Error(`shares update failed: ${r.status}`);
  return r.json();
}

async function postRatio(assetId, exchange, ratio, source, referenceFdv, confidence) {
  const r = await fetch(`${API_BASE}/api/admin/market/${assetId}/${exchange}/ratio`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      share_ratio: ratio,
      ratio_source: source,
      ratio_reference_fdv: referenceFdv,
      confidence,
    }),
  });
  if (!r.ok) throw new Error(`ratio update failed: ${r.status}`);
  return r.json();
}

// ============================================================
// master normalization table
// ============================================================
function NormTable({ asset, norm, onFdvShares }) {
  const [savingShares, setSavingShares] = useState(false);
  const [sharesDraft, setSharesDraft] = useState((asset.fdvShares/1e9).toFixed(2));

  // keep draft in sync when asset changes externally
  useEffect(() => {
    setSharesDraft((asset.fdvShares/1e9).toFixed(2));
  }, [asset.fdvShares]);

  async function commitShares() {
    const value = parseFloat(sharesDraft) * 1e9;
    if (!isFinite(value) || value <= 0) return;
    if (Math.abs(value - asset.fdvShares) < 1) return;
    setSavingShares(true);
    try {
      await postShares(asset.id, value, "manual edit via dashboard", 65);
      onFdvShares(value);
    } catch (e) {
      console.error("shares update failed", e);
      setSharesDraft((asset.fdvShares/1e9).toFixed(2));
    } finally {
      setSavingShares(false);
    }
  }

  async function calibrateRatio(row) {
    const current = row.shareRatio;
    const reply = window.prompt(
      `Edit share_ratio for ${row.ex}\n\n` +
      `Formula: implied_share_price = token_price / share_ratio\n` +
      `Current: ${current}\n` +
      `Token price: ${row.tokenPrice}\n\n` +
      `Options:\n` +
      `  • Direct ratio:    "2.83"\n` +
      `  • Backsolve from reference FDV:    "fdv:800B"   or   "fdv:1.75T"`,
      String(current)
    );
    if (reply == null) return;
    let newRatio;
    let referenceFdv = null;
    let source = "market-fitted";
    const trimmed = reply.trim();
    if (trimmed.toLowerCase().startsWith("fdv:")) {
      const rest = trimmed.slice(4).trim().toUpperCase();
      const num = parseFloat(rest);
      if (!isFinite(num) || num <= 0) return;
      // Parse trailing B / T / M suffix; default = B (most common pre-IPO scale)
      let mult = 1e9;
      if (rest.endsWith("T")) mult = 1e12;
      else if (rest.endsWith("M")) mult = 1e6;
      else if (rest.endsWith("B")) mult = 1e9;
      referenceFdv = num * mult;
      const refShare = referenceFdv / asset.fdvShares;
      newRatio = row.tokenPrice / refShare;
      source = "back-solved";
    } else {
      newRatio = parseFloat(trimmed);
    }
    if (!isFinite(newRatio) || newRatio <= 0) return;
    try {
      await postRatio(asset.id, row.ex, newRatio, source, referenceFdv, 55);
    } catch (e) {
      console.error("ratio update failed", e);
      window.alert("Ratio update failed: " + e.message);
    }
  }

  return (
    <div className="panel">
      <PanelHead
        title="NORMALIZATION MATRIX"
        sub={<>Implied share price = <span className="mono">token / ratio</span> · Implied FDV = <span className="mono">implied share × FDV shares</span></>}
        right={
          <div className="fdv-editor">
            <span className="muted">FDV SHARES</span>
            <input
              className="fdv-input mono"
              type="number"
              step="0.01"
              value={sharesDraft}
              disabled={savingShares}
              onChange={e => setSharesDraft(e.target.value)}
              onBlur={commitShares}
              onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }}
              title={`source: ${asset.sharesSource || "n/a"}\nconfidence: ${asset.sharesConfidence || 0}/100\nupdated: ${asset.sharesLastUpdated || "?"}`}
            />
            <span className="muted">B</span>
            {asset.sharesSourceUrl && (
              <a
                href={asset.sharesSourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="src-link mono"
                title={asset.sharesSource}
              >
                src↗
              </a>
            )}
          </div>
        }
      />
      <div className="table-wrap">
        <table className="ntable">
          <thead>
            <tr>
              <th className="l">VENUE</th>
              <th className="l">STRUCTURE</th>
              <th className="r">TOKEN PX</th>
              <th className="r">SHARE RATIO</th>
              <th className="l">RATIO SOURCE</th>
              <th className="r">IMPLIED $/SH</th>
              <th className="r">IMPLIED FDV</th>
              <th className="r">vs REF</th>
              <th className="r">vs CONS.</th>
              <th className="r">24H VOL</th>
              <th className="r">OI</th>
              <th className="r">SPRD bp</th>
              <th className="l">CONFIDENCE</th>
            </tr>
          </thead>
          <tbody>
            {[...norm.rows].sort((a,b)=>b.impliedFDV-a.impliedFDV).map((r,i)=> {
              const prem = (r.impliedFDV/norm.consensus - 1) * 100;
              const premRef = (r.impliedFDV/asset.lastRound.valuation - 1) * 100;
              return (
                <tr key={r.ex}>
                  <td className="l">
                    <div className="venue-cell">
                      <span className="venue-name">{r.ex}</span>
                      <span className="venue-type">{r.venue}</span>
                      {r.symbol && (
                        <span className="venue-ticker mono muted" title="exchange-canonical symbol">{r.symbol}</span>
                      )}
                      <TickAge tickAt={r.tickAt}/>
                    </div>
                  </td>
                  <td className="l">
                    <span className={"struct-pill tone-"+STRUCT_META[r.structure].tone}>{STRUCT_META[r.structure].sym}</span>
                    <span className="struct-label">{STRUCT_META[r.structure].label}</span>
                  </td>
                  <td className={"r mono price-cell " + (r.direction === "up" ? "flash-up" : r.direction === "down" ? "flash-down" : "")} key={"px-"+r.ex+"-"+r.flashAt}>{fmtNum(r.tokenPrice)}</td>
                  <td className="r mono">
                    <span
                      className="ratio-cell ratio-edit"
                      title="Click to edit share_ratio (or backsolve with 'fdv:<billions>')"
                      onClick={() => calibrateRatio(r)}
                      style={{cursor:"pointer", textDecoration:"underline dotted"}}
                    >
                      1 : {fmtNum(r.shareRatio, 4)}
                    </span>
                  </td>
                  <td className="l">
                    <span className={"src-dot dot-"+SRC_META[r.ratioSource].dot}/>
                    <span className="src-label">{SRC_META[r.ratioSource].label}</span>
                    {r.docUrl && (
                      <a
                        href={r.docUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="src-link mono"
                        title={
                          (r.ratioReferenceFdv
                            ? `ref FDV: $${fmtUSD(r.ratioReferenceFdv)}\n`
                            : "") +
                          (r.ratioCalibratedAt
                            ? `calibrated: ${r.ratioCalibratedAt}\n`
                            : "") +
                          `exchange docs: ${r.docUrl}`
                        }
                      >
                        ↗
                      </a>
                    )}
                  </td>
                  <td className="r mono accent">{fmtNum(r.impliedShare)}</td>
                  <td className="r mono accent strong">${fmtUSD(r.impliedFDV)}</td>
                  <td
                    className={"r mono strong " + (premRef>=0?"ok":"bad")}
                    title={`Implied FDV vs $${fmtUSD(asset.lastRound.valuation)} (${asset.lastRound.date} round)`}
                  >
                    {fmtPremium(premRef)}
                  </td>
                  <td className={"r mono " + (prem>=0?"ok":"bad")}>{fmtPct(prem,2)}</td>
                  <td className="r mono">${fmtUSD(r.vol24)}</td>
                  <td className="r mono">${fmtUSD(r.oi)}</td>
                  <td className="r mono">{r.spread}</td>
                  <td className="l">
                    <div className="conf-cell">
                      <div className="confbar small">
                        <div className={"confbar-fill " + confClass(r.confidence)} style={{width: r.confidence+"%"}}/>
                      </div>
                      <span className="mono conf-num">{r.confidence}</span>
                    </div>
                  </td>
                </tr>
              );
            })}
            {((window.DISABLED || {})[asset.id] || []).map((d) => (
              <tr key={"dis-"+d.ex} className="ntable-row-disabled">
                <td className="l">
                  <div className="venue-cell">
                    <span className="venue-name muted">{d.ex}</span>
                    <span className="venue-type muted">{d.venue || "?"}</span>
                    {d.symbol && (
                      <span className="venue-ticker mono muted" title="exchange-canonical symbol">{d.symbol}</span>
                    )}
                  </div>
                </td>
                <td className="l">
                  <span className={"struct-pill tone-"+(STRUCT_META[d.structure]?.tone || "neutral")}>
                    {STRUCT_META[d.structure]?.sym || "?"}
                  </span>
                  <span className="struct-label muted">{STRUCT_META[d.structure]?.label || d.structure}</span>
                </td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="l">
                  <span className="src-dot dot-bad"/>
                  <span className="src-label muted">{d.ratioSource || "uncalibrated"}</span>
                </td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="r mono muted">—</td>
                <td className="l" colSpan={1}>
                  <span
                    className="dis-badge mono"
                    title={d.reason || "disabled"}
                  >
                    DISABLED
                    {d.docUrl && (
                      <a
                        className="dis-link"
                        href={d.docUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e)=>e.stopPropagation()}
                      > ↗</a>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function confClass(c) {
  if (c >= 75) return "c-good";
  if (c >= 55) return "c-mid";
  if (c >= 35) return "c-warn";
  return "c-bad";
}

// ============================================================
// structure / source breakdown
// ============================================================
function StructureBreakdown({ norm }) {
  // group by structure. "Activity" = OI when available (perp), else vol24
  // (spot/mirror has no OI). This matches the consensus weighting logic so
  // mirror-credit venues don't disappear from the breakdown.
  const groups = {};
  norm.rows.forEach(r => {
    if (!groups[r.structure]) groups[r.structure] = { count:0, oi:0, vol:0, act:0, fdvSum:0, confSum:0 };
    const g = groups[r.structure];
    const act = (r.oi && r.oi > 0) ? r.oi : (r.vol24 || 0);
    g.count++; g.oi += r.oi; g.vol += r.vol24; g.act += act;
    g.fdvSum += r.impliedFDV; g.confSum += r.confidence;
  });
  const totalAct = Object.values(groups).reduce((s,v)=>s+v.act, 0) || 1;
  const arr = Object.entries(groups).map(([k,v]) => ({
    structure: k,
    count: v.count,
    oi: v.oi,
    vol: v.vol,
    avgFDV: v.fdvSum / v.count,
    avgConf: v.confSum / v.count,
    oiShare: v.act/totalAct*100   // re-labelled as activity share visually
  })).sort((a,b)=>b.oiShare-a.oiShare);

  return (
    <div className="panel">
      <PanelHead title="STRUCTURE COMPOSITION" sub="by activity share (OI for perp, 24h vol for spot)"/>
      <div className="struct-bar-row">
        {arr.map(g => (
          <div key={g.structure}
               className={"struct-seg tone-"+STRUCT_META[g.structure].tone}
               style={{width: g.oiShare+"%"}}
               title={g.structure + " " + g.oiShare.toFixed(1) + "%"}>
            <span className="struct-seg-pct mono">{g.oiShare.toFixed(0)}%</span>
          </div>
        ))}
      </div>
      <div className="struct-table">
        {arr.map(g => (
          <div key={g.structure} className="struct-row">
            <div className="struct-row-l">
              <span className={"struct-pill tone-"+STRUCT_META[g.structure].tone}>{STRUCT_META[g.structure].sym}</span>
              <span className="struct-row-name">{STRUCT_META[g.structure].label}</span>
              <span className="muted">×{g.count}</span>
            </div>
            <div className="struct-row-cells">
              <div className="cell"><div className="cell-l">OI SHARE</div><div className="cell-v mono">{g.oiShare.toFixed(1)}%</div></div>
              <div className="cell"><div className="cell-l">24H VOL</div><div className="cell-v mono">${fmtUSD(g.vol)}</div></div>
              <div className="cell"><div className="cell-l">AVG IMP. FDV</div><div className="cell-v mono">${fmtUSD(g.avgFDV)}</div></div>
              <div className="cell"><div className="cell-l">AVG CONF</div><div className="cell-v mono">{g.avgConf.toFixed(0)}</div></div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// FUNDING & CARRY panel
// ============================================================
function FundingPanel({ assetId, listings }) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(()=>setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  // next funding is at the next 8h boundary (00, 08, 16 UTC)
  const h = now.getUTCHours();
  const next = new Date(now);
  next.setUTCHours(Math.ceil((h+0.001)/8)*8, 0, 0, 0);
  const diff = Math.max(0, next - now);
  const hh = Math.floor(diff/3600000).toString().padStart(2,"0");
  const mm = Math.floor(diff%3600000/60000).toString().padStart(2,"0");
  const ss = Math.floor(diff%60000/1000).toString().padStart(2,"0");

  const rows = listings.map(l => {
    const isPerp = l.structure === "perp";
    // funding_rate from BE (OKX funding-rate channel). null for spot/mirror.
    const r = isPerp ? getFundingRate(l) : null;
    const apr = r == null ? null : fundingAPR(r);
    const hist = isPerp ? getFundingHistory(assetId, l.ex) : null;
    return { ...l, fundingR: r, fundingAPR: apr, hist };
  });
  const perpRows = rows.filter(r => r.fundingR != null);
  const maxAbs = (perpRows.length > 0
    ? Math.max(...perpRows.map(r => Math.abs(r.fundingR))) * 1.15
    : 0) || 1e-6; // avoid div-by-zero when all funding=0

  return (
    <div className="panel">
      <PanelHead
        title="FUNDING & CARRY"
        sub="Perp instruments only · 8h intervals · positive = longs pay shorts"
        right={
          <div className="countdown mono">
            <span className="muted">NEXT FUNDING</span>
            <span className="cd-time">{hh}:{mm}:{ss}</span>
          </div>
        }
      />
      <div className="funding-rows">
        {rows.map(r => {
          if (r.fundingR == null) {
            return (
              <div key={r.ex} className="funding-row na">
                <div className="fr-venue">
                  <span className={"struct-pill tone-"+STRUCT_META[r.structure].tone}>{STRUCT_META[r.structure].sym}</span>
                  <div className="fr-venue-name">{r.ex}</div>
                  <span className="muted fr-na-label">structural · no funding</span>
                </div>
              </div>
            );
          }
          const pct = (r.fundingR / maxAbs) * 50;
          const positive = r.fundingR >= 0;
          return (
            <div key={r.ex} className="funding-row">
              <div className="fr-venue">
                <span className={"struct-pill tone-"+STRUCT_META[r.structure].tone}>{STRUCT_META[r.structure].sym}</span>
                <div className="fr-venue-name">{r.ex}</div>
              </div>
              <div className="fr-bar">
                <div className="fr-bar-center"/>
                <div className={"fr-bar-fill " + (positive?"pos":"neg")}
                     style={{
                       left: positive ? "50%" : (50+pct)+"%",
                       width: Math.abs(pct)+"%"
                     }}/>
              </div>
              <div className={"fr-rate mono " + (positive?"ok":"bad")}>
                {(r.fundingR*100).toFixed(4)}%
              </div>
              <div className={"fr-apr mono " + (positive?"ok":"bad")}>
                {fmtPct(r.fundingAPR*100, 2)}
                <span className="muted fr-apr-l"> APR</span>
              </div>
              <FundingSpark hist={r.hist} positive={positive}/>
              <div className={"fr-dir mono " + (positive?"warn":"ok")}>
                {positive ? "L→S" : "S→L"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FundingSpark({ hist, positive }) {
  const w = 100, h = 18;
  if (!hist || hist.length < 2) {
    // Not enough history yet — show flat baseline + value dot
    const v = hist && hist.length === 1 ? hist[0] : 0;
    return (
      <svg className="fr-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <line x1="0" x2={w} y1={h/2} y2={h/2} stroke="#2a3744" strokeWidth="0.5" strokeDasharray="2 2"/>
        {hist && hist.length === 1 && (
          <circle cx={w-3} cy={h/2} r={1.5} fill={v >= 0 ? "var(--c-ok)" : "var(--c-bad)"}/>
        )}
      </svg>
    );
  }
  const min = Math.min(...hist, 0), max = Math.max(...hist, 0);
  const rng = (max - min) || 1;
  const y0 = h - ((0 - min)/rng) * h;
  const path = hist.map((p,i)=>`${i===0?"M":"L"}${(i/(hist.length-1))*w},${h - ((p-min)/rng)*h}`).join(" ");
  return (
    <svg className="fr-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <line x1="0" x2={w} y1={y0} y2={y0} stroke="#2a3744" strokeWidth="0.5" strokeDasharray="2 2"/>
      <path d={path} fill="none" stroke={positive ? "var(--c-ok)" : "var(--c-bad)"} strokeWidth="1.2"/>
    </svg>
  );
}

// ============================================================
// HEDGE BOOK — ranked long/short combos
// ============================================================
function HedgeBook({ assetId, norm }) {
  const combos = useMemo(() => buildHedgeBook(norm.rows, assetId).slice(0, 7), [norm, assetId]);
  if (combos.length === 0) return null;
  const best = combos[0];
  return (
    <div className="panel">
      <PanelHead
        title="HEDGE BOOK · BEST LONG/SHORT"
        sub="Long under-priced venue · short rich venue · normalized to $1M notional"
        right={
          <div className="hb-best mono">
            <span className="muted">TOP GAP</span>
            <span className="ok strong">+{best.gapPct.toFixed(2)}%</span>
            <span className="muted">≈ ${fmtUSD(best.gapUSD)}</span>
          </div>
        }
      />
      <div className="hb-list">
        <div className="hb-head mono">
          <div>#</div>
          <div>LONG (cheap)</div>
          <div>SHORT (rich)</div>
          <div className="r">GAP</div>
          <div className="r">FUNDING APR</div>
          <div className="r">CONV. P&amp;L · $1M</div>
          <div className="r">CARRY · $1M / mo</div>
          <div className="c">RISK</div>
          <div className="c">EXEC</div>
        </div>
        {combos.map((c, i) => {
          const fundingMonthlyOnMillion = (c.netFundingApr/100) * 1e6 / 12;
          const convergencePnL = (c.gapPct/100) * 1e6;
          return (
            <div key={i} className={"hb-row " + (i===0 ? "top" : "")}>
              <div className="hb-rank mono">{(i+1).toString().padStart(2,"0")}</div>
              <div className="hb-leg">
                <div className="hb-leg-top">
                  <span className={"struct-pill tone-"+STRUCT_META[c.long.structure].tone}>{STRUCT_META[c.long.structure].sym}</span>
                  <span className="hb-leg-ex">{c.long.ex}</span>
                </div>
                <div className="hb-leg-bot mono">
                  <span className="muted">@</span> ${fmtUSD(c.long.impliedFDV)}
                  {c.loFund != null && <span className="hb-fund muted"> · f {(c.loFund*100).toFixed(3)}%</span>}
                </div>
              </div>
              <div className="hb-leg">
                <div className="hb-leg-top">
                  <span className={"struct-pill tone-"+STRUCT_META[c.short.structure].tone}>{STRUCT_META[c.short.structure].sym}</span>
                  <span className="hb-leg-ex">{c.short.ex}</span>
                </div>
                <div className="hb-leg-bot mono">
                  <span className="muted">@</span> ${fmtUSD(c.short.impliedFDV)}
                  {c.hiFund != null && <span className="hb-fund muted"> · f {(c.hiFund*100).toFixed(3)}%</span>}
                </div>
              </div>
              <div className="hb-cell r mono ok strong">+{c.gapPct.toFixed(2)}%</div>
              <div className={"hb-cell r mono " + (c.netFundingApr>=0?"ok":"bad")}>{fmtPct(c.netFundingApr, 2)}</div>
              <div className="hb-cell r mono">${fmtUSD(convergencePnL)}</div>
              <div className={"hb-cell r mono " + (fundingMonthlyOnMillion>=0?"ok":"bad")}>
                {fundingMonthlyOnMillion>=0?"+":"−"}${fmtUSD(Math.abs(fundingMonthlyOnMillion))}
              </div>
              <div className="hb-cell c">
                <div className="hb-risk">
                  {c.mismatch && <span className="risk-chip warn" title="structure mismatch">MIX</span>}
                  {c.minConf < 40 && <span className="risk-chip bad" title="low confidence">CONF {c.minConf}</span>}
                  {!c.mismatch && c.minConf >= 70 && <span className="risk-chip ok">CLEAN</span>}
                  {!c.mismatch && c.minConf >= 40 && c.minConf < 70 && <span className="risk-chip neu">MID</span>}
                </div>
              </div>
              <div className="hb-cell c">
                <button className="exec-btn">→</button>
              </div>
            </div>
          );
        })}
      </div>
      <div className="hb-foot mono">
        <span className="muted">Conv P&amp;L assumes mean-reversion to consensus.</span>
        <span className="muted">Carry assumes flat funding for 30d.</span>
        <span className="muted">Risk: <span className="risk-chip warn inline">MIX</span> = SPV/perp structure delta · <span className="risk-chip bad inline">CONF</span> = ratio not officially documented</span>
      </div>
    </div>
  );
}

// ============================================================
// reusable panel head
// ============================================================
function PanelHead({ title, sub, right }) {
  return (
    <div className="panel-head">
      <div className="panel-head-l">
        <div className="panel-title">{title}</div>
        {sub && <div className="panel-sub">{sub}</div>}
      </div>
      {right && <div className="panel-head-r">{right}</div>}
    </div>
  );
}

// ============================================================
// ratio derivation panel (right column)
// ============================================================
function DerivationPanel({ asset, norm }) {
  // pick highest confidence row as anchor explanation
  const anchor = [...norm.rows].sort((a,b)=>b.confidence-a.confidence)[0];
  return (
    <div className="panel deriv-panel">
      <PanelHead title="DERIVATION" sub="how implied FDV is built"/>
      <div className="deriv-body">
        <div className="deriv-step">
          <div className="deriv-step-num">01</div>
          <div className="deriv-step-body">
            <div className="deriv-step-h">RAW TOKEN PRICE</div>
            <div className="deriv-formula mono">
              {anchor.ex} <span className="muted">→</span> <span className="accent">${fmtNum(anchor.tokenPrice)}</span>
            </div>
          </div>
        </div>
        <div className="deriv-arrow">↓</div>
        <div className="deriv-step">
          <div className="deriv-step-num">02</div>
          <div className="deriv-step-body">
            <div className="deriv-step-h">SHARE EXPOSURE RATIO</div>
            <div className="deriv-formula mono">
              1 token = <span className="accent">{fmtNum(anchor.shareRatio, 4)}</span> shares
            </div>
            <div className="deriv-note muted">source · {SRC_META[anchor.ratioSource].label}</div>
          </div>
        </div>
        <div className="deriv-arrow">↓</div>
        <div className="deriv-step">
          <div className="deriv-step-num">03</div>
          <div className="deriv-step-body">
            <div className="deriv-step-h">IMPLIED SHARE PRICE</div>
            <div className="deriv-formula mono">
              {fmtNum(anchor.tokenPrice)} / {fmtNum(anchor.shareRatio,4)} = <span className="accent">${fmtNum(anchor.impliedShare)}</span>
            </div>
          </div>
        </div>
        <div className="deriv-arrow">↓</div>
        <div className="deriv-step">
          <div className="deriv-step-num">04</div>
          <div className="deriv-step-body">
            <div className="deriv-step-h">FULLY DILUTED SHARES</div>
            <div className="deriv-formula mono">
              <span className="accent">{(asset.fdvShares/1e9).toFixed(2)}B</span> shares
            </div>
            <div className="deriv-note muted">last priced round · {asset.lastRound.date} · ${fmtUSD(asset.lastRound.valuation)}</div>
          </div>
        </div>
        <div className="deriv-arrow">↓</div>
        <div className="deriv-step final">
          <div className="deriv-step-num">05</div>
          <div className="deriv-step-body">
            <div className="deriv-step-h">IMPLIED FDV</div>
            <div className="deriv-formula mono big">
              <span className="accent strong">${fmtUSD(anchor.impliedShare * asset.fdvShares)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// VenueCards — at-a-glance grid comparing implied FDV / premium /
// funding / vol across exchanges for the selected asset. Replaces
// the heavy ValuationChart in the primary slot so the user can
// scan venues without parsing a time series.
// ============================================================
function VenueCards({ asset, norm }) {
  const refFdv = asset.lastRound.valuation;
  const cons = norm.consensus;
  const rows = [...norm.rows].sort((a, b) => b.impliedFDV - a.impliedFDV);
  const disabled = (window.DISABLED || {})[asset.id] || [];

  return (
    <div className="panel venue-cards-panel">
      <PanelHead
        title="VENUES · LIVE COMPARISON"
        sub={<>Implied FDV per exchange · premium vs <span className="mono">${fmtUSD(refFdv)}</span> last round · funding (perp only)</>}
        right={
          <div className="vc-legend mono muted">
            {rows.length} live · {disabled.length} disabled
          </div>
        }
      />
      <div className="venue-grid">
        {rows.map((r) => {
          const premRef = ((r.impliedFDV / refFdv) - 1) * 100;
          const premCons = cons > 0 ? ((r.impliedFDV / cons) - 1) * 100 : 0;
          const fPct = r.fundingRate != null ? r.fundingRate * 100 : null;
          const meta = STRUCT_META[r.structure] || { sym: "?", tone: "neutral", label: r.structure };
          // Hover-only detail block. Avoids cluttering the at-a-glance card.
          const hoverTitle =
            `${r.ex} · ${r.symbol || ""}\n` +
            `Token: $${fmtNum(r.tokenPrice)}\n` +
            `Ratio: 1 : ${fmtNum(r.shareRatio, 4)} (${SRC_META[r.ratioSource]?.label || r.ratioSource})\n` +
            `Confidence: ${r.confidence}/100`;
          return (
            <div
              key={r.ex}
              className={"venue-card " + (r.direction === "up" ? "flash-up" : r.direction === "down" ? "flash-down" : "")}
              title={hoverTitle}
            >
              <div className="vc-head">
                <div className="vc-head-l">
                  <span className="vc-name">{r.ex}</span>
                  <span className="vc-sub muted">{meta.label} · {r.venue}</span>
                  {r.symbol && (
                    <span className="vc-symbol mono muted" title="exchange-canonical symbol">{r.symbol}</span>
                  )}
                </div>
                <span className={"struct-pill tone-" + meta.tone}>{meta.sym}</span>
              </div>

              <div className="vc-main mono accent">${fmtUSD(r.impliedFDV)}</div>

              <div className="vc-stat">
                <span className="vc-stat-v mono">${fmtNum(r.tokenPrice)}</span>
                <span className="vc-stat-k muted">가격</span>
              </div>
              <div className="vc-stat">
                <span className={"vc-stat-v mono " + (premRef >= 0 ? "ok" : "bad")}>{fmtPremium(premRef)}</span>
                <span className="vc-stat-k muted">변동</span>
              </div>
              <div className="vc-stat">
                <span className="vc-stat-v mono">{r.vol24 ? "$" + fmtUSD(r.vol24) : "—"}</span>
                <span className="vc-stat-k muted">거래량</span>
              </div>
              <div className="vc-stat">
                <span className={"vc-stat-v mono " + (fPct == null ? "muted" : fPct >= 0 ? "warn" : "ok")}>
                  {fPct == null ? "—" : (fPct >= 0 ? "+" : "") + fPct.toFixed(4) + "%"}
                </span>
                <span className="vc-stat-k muted">펀비</span>
              </div>
              <div className="vc-stat">
                <span className={"vc-stat-v mono " + (premCons >= 0 ? "ok" : "bad")}>
                  {cons > 0 ? fmtPct(premCons, 2) : "—"}
                </span>
                <span className="vc-stat-k muted">갭(%)</span>
              </div>
            </div>
          );
        })}

        {disabled.map((d) => {
          const meta = STRUCT_META[d.structure] || { sym: "?", tone: "neutral", label: d.structure };
          return (
            <div key={"dis-" + d.ex} className="venue-card vc-disabled" title={d.reason}>
              <div className="vc-head">
                <div className="vc-head-l">
                  <span className="vc-name muted">{d.ex}</span>
                  <span className="vc-sub muted">{meta.label} · {d.venue || "?"}</span>
                </div>
                <span className={"struct-pill tone-" + meta.tone}>{meta.sym}</span>
              </div>
              <div className="vc-main mono muted">—</div>
              <div className="vc-stat">
                <span className="vc-stat-v mono muted">disabled</span>
                <span className="vc-stat-k muted">상태</span>
              </div>
              {d.symbol && (
                <div className="vc-stat">
                  <span className="vc-stat-v mono muted vc-ticker">{d.symbol}</span>
                  <span className="vc-stat-k muted">티커</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// Alert toast stack — listens to "pre-ipo:alert" CustomEvents
// ============================================================
function AlertToasts() {
  const [toasts, setToasts] = useState([]);
  useEffect(() => {
    const fn = (e) => {
      const t = {
        id: Math.random().toString(36).slice(2),
        message: e.detail.payload.message,
        kind: e.detail.rule.kind,
        at: e.detail.at,
      };
      setToasts(prev => [...prev.slice(-4), t]);
      setTimeout(() => {
        setToasts(prev => prev.filter(x => x.id !== t.id));
      }, 12_000);
    };
    window.addEventListener("pre-ipo:alert", fn);
    return () => window.removeEventListener("pre-ipo:alert", fn);
  }, []);
  if (toasts.length === 0) return null;
  return (
    <div className="alert-toasts">
      {toasts.map(t => (
        <div key={t.id} className={"alert-toast kind-" + t.kind.replace(/-/g, "_")}>
          <div className="alert-toast-kind mono">{t.kind}</div>
          <div className="alert-toast-msg">{t.message}</div>
          <button className="alert-toast-x" onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}>×</button>
        </div>
      ))}
    </div>
  );
}

function addPremiumAlert(assetId) {
  const reply = window.prompt(
    `Add premium alert for ${assetId}\n\n` +
    `Enter threshold (e.g. "30x", "+500%", "-50%"):\n` +
    `Fires once per minute when premium-vs-reference crosses the value.\n\n` +
    `Optional: prefix with venue, e.g. "okx 30x".`,
    "30x"
  );
  if (!reply) return;
  const parts = reply.trim().split(/\s+/);
  let exchange = "consensus";
  let token = parts[0];
  if (parts.length > 1) {
    exchange = parts[0].toLowerCase();
    token = parts.slice(1).join(" ");
  }
  const t = token.toLowerCase().replace(/^\+/, "");
  let value = 0;
  let kind = "premium-above";
  if (t.endsWith("x")) {
    const mult = parseFloat(t.slice(0, -1));
    if (!isFinite(mult)) return;
    value = (mult - 1) * 100;
  } else if (t.endsWith("%")) {
    value = parseFloat(t.slice(0, -1));
    if (!isFinite(value)) return;
  } else {
    value = parseFloat(t);
    if (!isFinite(value)) return;
  }
  if (value < 0) kind = "premium-below";
  if (token.startsWith("-")) kind = "premium-below";

  // request browser notification permission once
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
  const added = window.PRE_IPO_ALERTS.add({ assetId, exchange, kind, value });
  window.alert(`Alert added:\n  asset=${assetId}\n  venue=${exchange}\n  ${kind} ${value.toFixed(1)}%`);
  return added;
}

function AlertButton({ assetId }) {
  const [count, setCount] = useState(() => (window.PRE_IPO_ALERTS?.list() || []).filter(r => r.assetId === assetId).length);
  useEffect(() => {
    const tick = () => {
      setCount((window.PRE_IPO_ALERTS?.list() || []).filter(r => r.assetId === assetId).length);
    };
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, [assetId]);
  return (
    <button
      className="alert-btn mono"
      title="Add premium alert for this asset"
      onClick={() => { addPremiumAlert(assetId); setCount(c => c+1); }}
    >
      ALERTS {count > 0 ? `(${count})` : "+"}
    </button>
  );
}

// ============================================================
// app shell
// ============================================================
function App() {
  const [selectedId, setSelectedId] = useState("SPACEX");
  const [now, setNow] = useState(new Date());
  const [overrideShares, setOverrideShares] = useState({});
  // tickVersion is bumped by data-live.js via a custom DOM event. Acts as a
  // re-render trigger without rewriting useMemo data sources.
  const [tickVersion, setTickVersion] = useState(0);

  useEffect(() => {
    const t = setInterval(()=>setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const fn = () => setTickVersion(v => (v + 1) % 1_000_000);
    window.addEventListener("pre-ipo:tick", fn);
    return () => window.removeEventListener("pre-ipo:tick", fn);
  }, []);

  // attach sparkline data to assets
  const assets = useMemo(() => (window.ASSETS || []).map(a => {
    const seriesSet = (window.SERIES || {})[a.id];
    if (!seriesSet) return { ...a, _spark: [] };
    const venues = Object.keys(seriesSet);
    if (venues.length === 0) return { ...a, _spark: [] };
    const len = seriesSet[venues[0]].length;
    const avg = [];
    for (let i=0; i<len; i+=8) {
      let s = 0;
      let n = 0;
      venues.forEach(v => {
        const arr = seriesSet[v];
        if (arr && arr[i] != null) { s += arr[i]; n++; }
      });
      if (n > 0) avg.push(s / n);
    }
    return { ...a, _spark: avg };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [tickVersion]);

  // normalize every asset
  const normMap = useMemo(() => {
    const out = {};
    assets.forEach(a => {
      const shares = overrideShares[a.id] || a.fdvShares;
      const listings = (window.LISTINGS || {})[a.id];
      if (!listings || listings.length === 0) {
        out[a.id] = { rows: [], consensus: 0, max: null, min: null, avgConf: 0, spread: 0 };
      } else {
        out[a.id] = normalize(listings, shares);
      }
    });
    return out;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets, overrideShares, tickVersion]);

  const asset = useMemo(() => {
    const a = assets.find(x => x.id === selectedId);
    if (!a) return null;
    return { ...a, fdvShares: overrideShares[a.id] || a.fdvShares };
  }, [assets, selectedId, overrideShares]);
  if (!asset) {
    return (
      <div className="app">
        <TopBar now={now} asset={null}/>
        <div className="body" style={{padding:24,color:"var(--tx-2)"}}>
          Waiting for live snapshot… (asset {selectedId} not yet loaded)
        </div>
      </div>
    );
  }
  const norm = normMap[selectedId];
  const listings = (window.LISTINGS || {})[selectedId] || [];
  const series = (window.SERIES || {})[selectedId] || {};

  return (
    <div className="app">
      <TopBar now={now} asset={asset}/>
      <AlertToasts />
      <div className="body">
        <AssetRail assets={assets} selectedId={selectedId} onSelect={setSelectedId} normMap={normMap}/>
        <main className="main" data-screen-label="01 Valuation Dashboard">
          <AssetHeader asset={asset} norm={norm}/>
          <KPIStrip asset={asset} norm={norm}/>
          <VenueCards asset={asset} norm={norm}/>
          <div className="grid-2">
            <PremiumBars norm={norm}/>
            <DerivationPanel asset={asset} norm={norm}/>
          </div>
          <HedgeBook assetId={selectedId} norm={norm}/>
          <FundingPanel assetId={selectedId} listings={listings}/>
          <NormTable asset={asset} norm={norm} onFdvShares={(v)=>setOverrideShares(s=>({...s, [selectedId]: v}))}/>
          <StructureBreakdown norm={norm}/>
          <ValuationChart asset={asset} listings={listings} series={series} norm={norm}/>
          <FootBar/>
        </main>
      </div>
    </div>
  );
}

function FootBar() {
  const live = (typeof window !== "undefined" && window.PRE_IPO_LIVE) || {};
  const lastSnap = live.lastSnapshotAt ? new Date(live.lastSnapshotAt) : null;
  const ageSec = lastSnap ? Math.floor((Date.now() - lastSnap.getTime()) / 1000) : null;
  return (
    <footer className="footbar mono">
      <span>v0.2.0 · normalizer-engine</span>
      <span className="sep">·</span>
      <span>data feeds: OKX WS · Bitget REST · Hyperliquid (disabled, HIP-3 deferred)</span>
      <span className="sep">·</span>
      <span className="muted">
        {ageSec != null ? `snapshot ${ageSec}s ago` : "snapshot pending"}
      </span>
      <span className="grow"/>
      <span>{live.connected ? "WS LIVE" : "WS RECONNECTING"}</span>
    </footer>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
