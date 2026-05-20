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
    // Pass the BE literal through verbatim. The previous implementation
    // collapsed every unknown value (including a future Pydantic literal
    // and the "uncalibrated" sentinel) to "market-fitted", which silently
    // mislabeled provenance — operators saw the red Market-fitted badge on
    // a row that may have come from an entirely different source. The FE
    // SRC_META lookup degrades gracefully via a fallback object on the
    // app.jsx side, so we no longer need to whitelist.
    return m.ratio_source || "uncalibrated";
  }

  function isLiveMarket(m) {
    // A market that's "live" enough to keep in LISTINGS / drive a tick row.
    // Disabled / uncalibrated / zero-or-negative ratios are routed to
    // DISABLED instead (or simply dropped from LISTINGS on a config update).
    if (!m) return false;
    if (m.share_ratio == null) return false;
    if (typeof m.share_ratio !== "number" || !(m.share_ratio > 0)) return false;
    if (m.ratio_source === "uncalibrated") return false;
    return true;
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
      // Funding settlement cadence in hours — 8 for OKX/BingX, 1 for HL.
      // The FE uses this to annualize correctly; defaults to 8 if missing
      // (most CEX venues). Slice 5 F65.
      fundingIntervalHours: quote.funding_interval_hours != null ? quote.funding_interval_hours : 8,
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
    // Slice 5 F69: key by `market_id` (asset_id:exchange) so 3 OKX markets
    // sharing exchange="okx" don't all collapse to one status — the prior
    // implementation used `exchange` as the key and let an unhealthy peer
    // grey out healthy siblings.
    const statusByMarketId = {};
    (statusList || []).forEach((s) => {
      const mid = s && s.market_id ? s.market_id : null;
      if (mid) statusByMarketId[mid] = s;
    });
    const next = {};
    Object.values(marketsById).forEach((m) => {
      const mid = `${m.asset_id}:${m.exchange}`;
      const st = statusByMarketId[mid];
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
    if (!msg || typeof msg !== "object") return;
    const assets = Array.isArray(msg.assets) ? msg.assets : [];
    const markets = Array.isArray(msg.markets) ? msg.markets : [];
    const statuses = Array.isArray(msg.statuses) ? msg.statuses : [];
    // Index metadata
    Object.keys(assetsById).forEach((k) => delete assetsById[k]);
    assets.forEach((a) => {
      if (a && typeof a.id === "string") assetsById[a.id] = a;
    });
    Object.keys(marketsById).forEach((k) => delete marketsById[k]);
    markets.forEach((m) => {
      if (!m || typeof m.asset_id !== "string" || typeof m.exchange !== "string") return;
      const id = `${m.asset_id}:${m.exchange}`;
      marketsById[id] = m;
    });
    rebuildDisabled(statuses);

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
          if (mkt && isLiveMarket(mkt) && last.calibration_status !== "uncalibrated") {
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
    if (!quote || typeof quote.asset_id !== "string" || typeof quote.exchange !== "string") return;
    if (typeof quote.token_price !== "number" || !isFinite(quote.token_price)) return;
    const asset_id = quote.asset_id;
    const exchange = quote.exchange;
    const market = marketsById[`${asset_id}:${exchange}`];
    if (!market) return;
    // share_ratio <= 0 is treated identically to null — a bad calibrate write
    // can zero the ratio and would otherwise propagate Infinity into the
    // consensus rollup at line ~305.
    if (quote.calibration_status === "uncalibrated" || !isLiveMarket(market)) {
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
    if (!msg || typeof msg !== "object") return;
    const statuses = Array.isArray(msg.statuses) ? msg.statuses : [];
    window.PRE_IPO_LIVE.statuses = statuses;
    rebuildDisabled(statuses);
    scheduleDispatch();
  }

  function handleConfigUpdate(msg) {
    if (!msg || typeof msg !== "object") return;
    const markets = Array.isArray(msg.markets) ? msg.markets : [];
    const assets = Array.isArray(msg.assets) ? msg.assets : [];
    // Refresh markets metadata so the next tick uses the new share_ratio.
    // Also patch the ASSETS entry (fully_diluted_shares, headline, etc.) so
    // FDV editors and the AssetHeader pick up admin edits without a reload.
    markets.forEach((m) => {
      if (!m || typeof m.asset_id !== "string" || typeof m.exchange !== "string") return;
      marketsById[`${m.asset_id}:${m.exchange}`] = m;
    });
    rebuildDisabled(window.PRE_IPO_LIVE.statuses);
    assets.forEach((a) => {
      if (!a || typeof a.id !== "string") return;
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
    // Critically: a market that flipped to uncalibrated (or zeroed its ratio)
    // must drop OUT of LISTINGS — otherwise the row keeps showing as live
    // with a stale ratio + relabelled provenance, and rebuildDisabled adds
    // a contradictory ghost row right next to it.
    Object.keys(window.LISTINGS || {}).forEach((asset_id) => {
      const arr = window.LISTINGS[asset_id];
      for (let i = arr.length - 1; i >= 0; i--) {
        const mkt = marketsById[`${asset_id}:${arr[i].ex}`];
        if (!mkt || !isLiveMarket(mkt)) {
          arr.splice(i, 1);
          continue;
        }
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
    // Defensive: localStorage["preIpoAlerts"] is operator-mutable in DevTools
    // and a partially-typed entry (e.g. {} or [null]) would crash
    // evaluateAlerts on every tick — `filter is not a function` or null
    // deref — and freeze live updates. Normalize to "array of well-formed
    // rule objects" before returning.
    let raw;
    try {
      raw = JSON.parse(localStorage.getItem(ALERT_KEY) || "[]");
    } catch (e) {
      return [];
    }
    if (!Array.isArray(raw)) return [];
    const valid = raw.filter((r) =>
      r && typeof r === "object" &&
      typeof r.assetId === "string" &&
      typeof r.kind === "string" &&
      typeof r.value === "number" && isFinite(r.value)
    );
    if (valid.length !== raw.length) {
      // Self-heal: overwrite with the validated subset so the next load is
      // fast and the operator sees the cleaned list in DevTools.
      try { localStorage.setItem(ALERT_KEY, JSON.stringify(valid)); } catch (e) {}
    }
    return valid;
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
      if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return;
      // Per-handler try/catch: a thrown handler (e.g. unexpected payload
      // shape that slipped past the shape guards) must not bork the
      // onmessage subscription — otherwise the next frame is lost too.
      try {
        if (msg.type === "snapshot") handleSnapshot(msg);
        else if (msg.type === "tick") handleTick(msg.quote);
        else if (msg.type === "status") handleStatus(msg);
        else if (msg.type === "config-update") handleConfigUpdate(msg);
      } catch (e) {
        console.warn("[live] handler error", msg.type, e);
      }
    };
    ws.onerror = (e) => {
      console.warn("[live] ws error", e);
    };
    ws.onclose = () => {
      window.PRE_IPO_LIVE.connected = false;
      console.warn("[live] ws closed; will reconnect");
      // Detach handlers from the closing instance so any in-flight frame
      // delivered between onclose and GC doesn't fire stale-closure logic,
      // and the next reconnect's onopen can't race a parked handler.
      const dead = ws;
      ws = null;
      if (dead) {
        dead.onmessage = null;
        dead.onopen = null;
        dead.onerror = null;
        dead.onclose = null;
      }
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
