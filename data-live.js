/* data-live.js — replaces data.js with a live WebSocket feed.
 *
 * Exposes window.ASSETS / window.LISTINGS / window.SERIES with the same shape
 * as the prototype's static data.js, but kept in sync with the backend at
 * ws://127.0.0.1:8000/ws.
 *
 * Triggers UI re-renders by dispatching a custom DOM event "pre-ipo:tick"
 * whenever data changes; App.jsx listens for it and bumps a tickVersion
 * state which is wired as a useMemo dependency.
 *
 * Designed to survive 24/7 in a browser tab:
 *   - exponential-backoff reconnect (1s -> 30s cap)
 *   - 96-point ring buffer per (asset, exchange) — no unbounded growth
 *   - rAF-throttled event dispatch — never more than ~30 Hz UI updates
 */
(function () {
  "use strict";

  const WS_URL = (function () {
    // If served from FastAPI (port 8000), use same-origin. Else (file://,
    // static :8080 dev, etc.) fall back to backend on localhost:8000.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    if (window.location.hostname && window.location.port === "8000") {
      return `${proto}//${window.location.host}/ws`;
    }
    const host = window.location.hostname || "127.0.0.1";
    return `${proto}//${host}:8000/ws`;
  })();

  const RING_POINTS = 96;
  const FUNDING_RING_POINTS = 32;
  const DISPATCH_MIN_INTERVAL_MS = 33; // ~30Hz cap

  // ---- public globals (same shape as data.js) ----
  window.ASSETS = [];
  window.LISTINGS = {};
  window.SERIES = {};
  // FUNDING[asset_id][exchange] = bounded array of recent funding_rate values
  // (most recent at the end). null/undefined values are dropped. Spot/mirror
  // markets never appear here.
  window.FUNDING = {};
  // DISABLED[asset_id] = [{ex, venue, structure, reason, docUrl}] — venues
  // we know about but can't stream (uncalibrated or supervisor-disabled).
  // NormTable renders these as ghost rows so users see what's missing.
  window.DISABLED = {};
  window.PRE_IPO_LIVE = {
    connected: false,
    lastSnapshotAt: null,
    lastTickAt: null,
    statuses: [],
    wsUrl: WS_URL,
  };

  // ---- internal state ----
  const marketsById = {}; // "SPACEX:okx" -> Market metadata
  const assetsById = {}; // "SPACEX" -> Asset
  let pendingDispatch = false;
  let lastDispatch = 0;

  function structureFromMarket(m) {
    return m.structure || "perp";
  }

  function ratioSourceFromMarket(m) {
    // Map BE source values to prototype's narrower set; "uncalibrated"
    // venues are never added to LISTINGS so we don't need that case.
    const src = m.ratio_source;
    if (src === "official") return "official";
    if (src === "whitepaper") return "whitepaper";
    if (src === "back-solved") return "back-solved";
    return "market-fitted";
  }

  function makeListingRow(quote, market, prev) {
    // direction = up | down | flat — derived from previous tick price.
    let direction = null;
    let flashAt = prev ? prev.flashAt : null;
    if (prev && prev.tokenPrice != null && prev.tokenPrice !== quote.token_price) {
      direction = quote.token_price > prev.tokenPrice ? "up" : "down";
      flashAt = Date.now();
    }
    return {
      ex: market.exchange,
      symbol: market.symbol || null,
      venue: market.venue || "CEX",
      structure: structureFromMarket(market),
      ratioSource: ratioSourceFromMarket(market),
      tokenPrice: quote.token_price,
      shareRatio: market.share_ratio,
      confidence: quote.confidence,
      vol24: quote.vol24 || 0,
      oi: quote.oi || 0,
      spread: quote.spread_bp != null ? Math.round(quote.spread_bp) : 0,
      // null for spot/mirror (no funding); decimal fraction (e.g. 0.0001) per period
      fundingRate: quote.funding_rate,
      // calibration_status passed through for FE badges
      calibrationStatus: quote.calibration_status,
      // exchange docs URL — surfaced on RATIO SOURCE cell hover/click
      docUrl: market.doc_url || null,
      ratioReferenceFdv: market.ratio_reference_fdv,
      ratioCalibratedAt: market.ratio_calibrated_at,
      // tick freshness + flash
      tickAt: Date.now(),
      direction,
      flashAt,
    };
  }

  function makeAssetRow(asset, listings, history) {
    // Approximate delta24h from oldest vs newest implied_fdv in history.
    let delta24h = 0;
    let consensusFDV = 0;
    if (history && Object.keys(history).length) {
      let firsts = [];
      let lasts = [];
      Object.values(history).forEach((pts) => {
        if (pts.length > 0) {
          if (pts[0].implied_fdv != null) firsts.push(pts[0].implied_fdv);
          const last = pts[pts.length - 1];
          if (last.implied_fdv != null) lasts.push(last.implied_fdv);
        }
      });
      if (firsts.length && lasts.length) {
        const avgFirst = firsts.reduce((a, b) => a + b, 0) / firsts.length;
        const avgLast = lasts.reduce((a, b) => a + b, 0) / lasts.length;
        if (avgFirst > 0) delta24h = ((avgLast - avgFirst) / avgFirst) * 100;
        consensusFDV = avgLast;
      }
    }
    return {
      id: asset.id,
      ticker: asset.ticker,
      name: asset.name,
      sector: asset.sector,
      lastRound: {
        date: asset.last_round.date,
        price: asset.last_round.price,
        valuation: asset.last_round.valuation,
      },
      fdvShares: asset.fully_diluted_shares,
      consensusFDV: consensusFDV,
      delta24h: delta24h,
      headline: asset.headline,
      sharesSource: asset.shares_source,
      sharesSourceUrl: asset.shares_source_url,
      sharesConfidence: asset.shares_confidence,
      sharesLastUpdated: asset.shares_last_updated,
    };
  }

  function pushSeries(asset_id, exchange, implied_fdv) {
    if (implied_fdv == null) return;
    if (!window.SERIES[asset_id]) window.SERIES[asset_id] = {};
    if (!window.SERIES[asset_id][exchange]) window.SERIES[asset_id][exchange] = [];
    const arr = window.SERIES[asset_id][exchange];
    arr.push(implied_fdv);
    while (arr.length > RING_POINTS) arr.shift();
  }

  function pushFunding(asset_id, exchange, funding_rate) {
    if (funding_rate == null) return;
    if (!window.FUNDING[asset_id]) window.FUNDING[asset_id] = {};
    if (!window.FUNDING[asset_id][exchange]) window.FUNDING[asset_id][exchange] = [];
    const arr = window.FUNDING[asset_id][exchange];
    // de-dupe consecutive identical values to keep the buffer meaningful
    if (arr.length > 0 && arr[arr.length - 1] === funding_rate) return;
    arr.push(funding_rate);
    while (arr.length > FUNDING_RING_POINTS) arr.shift();
  }

  function rebuildDisabled(statusList) {
    // Rebuild window.DISABLED from current markets + adapter statuses.
    const statusByExchange = {};
    (statusList || []).forEach((s) => {
      // statuses may have multiple entries per exchange (one per asset);
      // pick the most informative non-connected state.
      const prev = statusByExchange[s.exchange];
      if (!prev || s.state !== "connected") {
        statusByExchange[s.exchange] = s;
      }
    });
    const next = {};
    Object.values(marketsById).forEach((m) => {
      const st = statusByExchange[m.exchange];
      const isDisabled =
        m.share_ratio == null ||
        m.ratio_source === "uncalibrated" ||
        (st && (st.state === "disabled" || st.state === "disconnected"));
      if (!isDisabled) return;
      if (!next[m.asset_id]) next[m.asset_id] = [];
      next[m.asset_id].push({
        ex: m.exchange,
        venue: m.venue,
        structure: m.structure,
        symbol: m.symbol,
        reason: (st && st.last_error) || "uncalibrated",
        docUrl: m.doc_url || null,
        ratioSource: m.ratio_source,
      });
    });
    window.DISABLED = next;
  }

  function handleSnapshot(msg) {
    // Index metadata
    Object.keys(assetsById).forEach((k) => delete assetsById[k]);
    (msg.assets || []).forEach((a) => {
      assetsById[a.id] = a;
    });
    Object.keys(marketsById).forEach((k) => delete marketsById[k]);
    (msg.markets || []).forEach((m) => {
      const id = `${m.asset_id}:${m.exchange}`;
      marketsById[id] = m;
    });
    rebuildDisabled(msg.statuses);

    // Reset LISTINGS / SERIES / FUNDING from history
    window.LISTINGS = {};
    window.SERIES = {};
    window.FUNDING = {};
    const history = msg.history || {};
    Object.keys(history).forEach((asset_id) => {
      const exMap = history[asset_id];
      window.SERIES[asset_id] = {};
      Object.keys(exMap).forEach((exchange) => {
        const pts = exMap[exchange];
        const series = pts
          .map((q) => q.implied_fdv)
          .filter((v) => v != null);
        window.SERIES[asset_id][exchange] = series.slice(-RING_POINTS);

        // seed FUNDING from any non-null funding_rate, de-duped
        const fundingPts = pts
          .map((q) => q.funding_rate)
          .filter((v) => v != null);
        if (fundingPts.length > 0) {
          const deduped = [];
          for (const v of fundingPts) {
            if (deduped.length === 0 || deduped[deduped.length - 1] !== v) {
              deduped.push(v);
            }
          }
          if (!window.FUNDING[asset_id]) window.FUNDING[asset_id] = {};
          window.FUNDING[asset_id][exchange] = deduped.slice(-FUNDING_RING_POINTS);
        }

        // also seed LISTINGS from the last point we have
        if (pts.length > 0) {
          const last = pts[pts.length - 1];
          const mkt = marketsById[`${asset_id}:${exchange}`];
          if (mkt && mkt.share_ratio != null && last.calibration_status !== "uncalibrated") {
            if (!window.LISTINGS[asset_id]) window.LISTINGS[asset_id] = [];
            window.LISTINGS[asset_id] = window.LISTINGS[asset_id].filter(
              (r) => r.ex !== exchange
            );
            window.LISTINGS[asset_id].push(makeListingRow(last, mkt));
          }
        }
      });
    });

    // Build ASSETS array from registry + history-derived metrics
    window.ASSETS = (msg.assets || []).map((a) =>
      makeAssetRow(a, window.LISTINGS[a.id], history[a.id])
    );

    window.PRE_IPO_LIVE.statuses = msg.statuses || [];
    window.PRE_IPO_LIVE.lastSnapshotAt = Date.now();
    scheduleDispatch();
  }

  function handleTick(quote) {
    const asset_id = quote.asset_id;
    const exchange = quote.exchange;
    const market = marketsById[`${asset_id}:${exchange}`];
    if (!market) return;
    if (quote.calibration_status === "uncalibrated" || market.share_ratio == null) {
      return;
    }

    // Update LISTINGS row in place
    if (!window.LISTINGS[asset_id]) window.LISTINGS[asset_id] = [];
    const arr = window.LISTINGS[asset_id];
    const idx = arr.findIndex((r) => r.ex === exchange);
    const prev = idx >= 0 ? arr[idx] : null;
    const row = makeListingRow(quote, market, prev);
    if (idx >= 0) arr[idx] = row;
    else arr.push(row);
    evaluateAlerts(asset_id, exchange, quote, prev);

    pushSeries(asset_id, exchange, quote.implied_fdv);
    pushFunding(asset_id, exchange, quote.funding_rate);

    // Update ASSETS delta24h on every tick is overkill; just bump consensusFDV
    const asset = window.ASSETS.find((a) => a.id === asset_id);
    if (asset && quote.implied_fdv != null) {
      // rolling consensus from LISTINGS. Size proxy = oi if available (perp)
      // else vol24 (spot/mirror). Both USD-denominated by adapters.
      let wsum = 0;
      let vsum = 0;
      arr.forEach((r) => {
        const size = (r.oi && r.oi > 0) ? r.oi : (r.vol24 || 0);
        const w = (r.confidence / 100) * Math.sqrt(Math.max(size, 1));
        const impl = r.tokenPrice / r.shareRatio;
        const fdv = impl * asset.fdvShares;
        wsum += w;
        vsum += w * fdv;
      });
      if (wsum > 0) asset.consensusFDV = vsum / wsum;
    }

    window.PRE_IPO_LIVE.lastTickAt = Date.now();
    scheduleDispatch();
  }

  function handleStatus(msg) {
    window.PRE_IPO_LIVE.statuses = msg.statuses || [];
    rebuildDisabled(msg.statuses);
    scheduleDispatch();
  }

  function handleConfigUpdate(msg) {
    // Refresh markets metadata so the next tick uses the new share_ratio.
    // Also patch the ASSETS entry (fully_diluted_shares, headline, etc.) so
    // FDV editors and the AssetHeader pick up admin edits without a reload.
    (msg.markets || []).forEach((m) => {
      marketsById[`${m.asset_id}:${m.exchange}`] = m;
    });
    rebuildDisabled(window.PRE_IPO_LIVE.statuses);
    (msg.assets || []).forEach((a) => {
      assetsById[a.id] = a;
      const existing = (window.ASSETS || []).find((x) => x.id === a.id);
      if (existing) {
        existing.fdvShares = a.fully_diluted_shares;
        existing.headline = a.headline;
        existing.sector = a.sector;
        existing.lastRound = {
          date: a.last_round.date,
          price: a.last_round.price,
          valuation: a.last_round.valuation,
        };
        existing.sharesSource = a.shares_source;
        existing.sharesSourceUrl = a.shares_source_url;
        existing.sharesConfidence = a.shares_confidence;
        existing.sharesLastUpdated = a.shares_last_updated;
      }
    });
    // Re-emit listings so confidence/ratio_source/share_ratio refresh too.
    Object.keys(window.LISTINGS || {}).forEach((asset_id) => {
      const arr = window.LISTINGS[asset_id];
      for (let i = 0; i < arr.length; i++) {
        const mkt = marketsById[`${asset_id}:${arr[i].ex}`];
        if (!mkt || mkt.share_ratio == null) continue;
        arr[i] = {
          ...arr[i],
          structure: structureFromMarket(mkt),
          ratioSource: ratioSourceFromMarket(mkt),
          shareRatio: mkt.share_ratio,
          confidence: mkt.confidence,
        };
      }
    });
    scheduleDispatch();
  }

  function scheduleDispatch() {
    if (pendingDispatch) return;
    pendingDispatch = true;
    const now = Date.now();
    const wait = Math.max(0, DISPATCH_MIN_INTERVAL_MS - (now - lastDispatch));
    setTimeout(() => {
      pendingDispatch = false;
      lastDispatch = Date.now();
      window.dispatchEvent(new CustomEvent("pre-ipo:tick"));
    }, wait);
  }

  // ---- Alerts ----
  // Persisted rules in localStorage under "preIpoAlerts":
  //   [{ id, assetId, exchange?: 'consensus'|'okx'|'bitget', kind: 'premium-above'|'premium-below'|'price-above'|'price-below', value, cooldownMs?, lastFiredAt? }]
  // value: for premium-above/-below this is a decimal multiplier (e.g. 30 for +30x)
  //        OR percent (e.g. 500 for +500%). We unify on "premium percent" internally.
  const ALERT_KEY = "preIpoAlerts";
  const ALERT_COOLDOWN_MS = 60_000;

  function loadAlerts() {
    try {
      return JSON.parse(localStorage.getItem(ALERT_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }
  function saveAlerts(arr) {
    try {
      localStorage.setItem(ALERT_KEY, JSON.stringify(arr));
    } catch (e) {}
  }
  window.PRE_IPO_ALERTS = {
    list: () => loadAlerts(),
    add: (rule) => {
      const list = loadAlerts();
      const next = {
        id: Date.now() + ":" + Math.random().toString(36).slice(2, 8),
        ...rule,
      };
      list.push(next);
      saveAlerts(list);
      return next;
    },
    remove: (id) => {
      saveAlerts(loadAlerts().filter((r) => r.id !== id));
    },
    clear: () => saveAlerts([]),
  };

  function fireAlert(rule, payload) {
    // throttle: don't refire within cooldown
    const now = Date.now();
    const list = loadAlerts();
    const live = list.find((r) => r.id === rule.id);
    if (live && live.lastFiredAt && now - live.lastFiredAt < (live.cooldownMs || ALERT_COOLDOWN_MS)) {
      return;
    }
    if (live) {
      live.lastFiredAt = now;
      saveAlerts(list);
    }
    const detail = { rule, payload, at: now };
    window.dispatchEvent(new CustomEvent("pre-ipo:alert", { detail }));
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      try {
        new Notification(`${rule.assetId} ${rule.kind}`, {
          body: payload.message,
          tag: rule.id,
        });
      } catch (e) {}
    }
  }

  function evaluateAlerts(asset_id, exchange, quote, prevRow) {
    const rules = loadAlerts().filter((r) => r.assetId === asset_id);
    if (rules.length === 0) return;
    const asset = assetsById[asset_id];
    if (!asset) return;
    const refFdv = asset.last_round && asset.last_round.valuation;
    if (!refFdv) return;
    const impliedFdv = quote.implied_fdv;
    if (impliedFdv == null) return;
    const premPct = ((impliedFdv - refFdv) / refFdv) * 100;
    const prevPremPct = prevRow && prevRow.tokenPrice && prevRow.shareRatio
      ? (((prevRow.tokenPrice / prevRow.shareRatio) * asset.fully_diluted_shares - refFdv) / refFdv) * 100
      : null;

    rules.forEach((rule) => {
      // exchange filter: 'consensus' or specific exchange
      if (rule.exchange && rule.exchange !== "consensus" && rule.exchange !== exchange) return;
      if (rule.kind === "premium-above") {
        const target = rule.value; // percent
        if (premPct >= target && (prevPremPct == null || prevPremPct < target)) {
          fireAlert(rule, {
            message: `${asset_id} ${exchange} premium ${premPct.toFixed(1)}% crossed +${target}%`,
            premPct,
          });
        }
      } else if (rule.kind === "premium-below") {
        const target = rule.value;
        if (premPct <= target && (prevPremPct == null || prevPremPct > target)) {
          fireAlert(rule, {
            message: `${asset_id} ${exchange} premium ${premPct.toFixed(1)}% dropped below ${target}%`,
            premPct,
          });
        }
      } else if (rule.kind === "price-above") {
        const target = rule.value;
        if (quote.token_price >= target && (!prevRow || prevRow.tokenPrice < target)) {
          fireAlert(rule, {
            message: `${asset_id} ${exchange} price $${quote.token_price.toFixed(2)} crossed above $${target}`,
            price: quote.token_price,
          });
        }
      } else if (rule.kind === "price-below") {
        const target = rule.value;
        if (quote.token_price <= target && (!prevRow || prevRow.tokenPrice > target)) {
          fireAlert(rule, {
            message: `${asset_id} ${exchange} price $${quote.token_price.toFixed(2)} dropped below $${target}`,
            price: quote.token_price,
          });
        }
      }
    });
  }

  // ---- WS lifecycle ----
  let ws = null;
  let reconnectAttempt = 0;

  function connect() {
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      console.error("[live] WebSocket construct failed", e);
      scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      reconnectAttempt = 0;
      window.PRE_IPO_LIVE.connected = true;
      console.info("[live] ws connected", WS_URL);
      scheduleDispatch();
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        console.warn("[live] bad json", e);
        return;
      }
      if (msg.type === "snapshot") handleSnapshot(msg);
      else if (msg.type === "tick") handleTick(msg.quote);
      else if (msg.type === "status") handleStatus(msg);
      else if (msg.type === "config-update") handleConfigUpdate(msg);
    };
    ws.onerror = (e) => {
      console.warn("[live] ws error", e);
    };
    ws.onclose = () => {
      window.PRE_IPO_LIVE.connected = false;
      console.warn("[live] ws closed; will reconnect");
      scheduleDispatch();
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    reconnectAttempt++;
    const backoff = Math.min(1000 * 2 ** Math.min(reconnectAttempt, 5), 30000);
    setTimeout(connect, backoff);
  }

  connect();
})();
