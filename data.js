// Pre-IPO synthetic instrument data
// Core thesis: do NOT compare token prices directly.
// Normalize synthetic instruments → implied company valuation.

window.ASSETS = [
  {
    id: "SPACEX",
    ticker: "SPX.PRE",
    name: "SpaceX",
    sector: "Aerospace / Space",
    lastRound: { date: "2025-12", price: 185.00, valuation: 350e9 },
    fdvShares: 1.89e9,
    consensusFDV: 612e9,
    delta24h: +2.34,
    headline: "Starship V3 cadence ramp; Starlink IPO carve-out chatter"
  },
  {
    id: "OPENAI",
    ticker: "OAI.PRE",
    name: "OpenAI",
    sector: "AI / Foundation Models",
    lastRound: { date: "2025-10", price: 210.00, valuation: 500e9 },
    fdvShares: 2.40e9,
    consensusFDV: 580e9,
    delta24h: -1.12,
    headline: "Tender offer at $500B closes; secondary demand 3.2x oversubscribed"
  },
  {
    id: "XAI",
    ticker: "XAI.PRE",
    name: "xAI",
    sector: "AI / Infrastructure",
    lastRound: { date: "2025-12", price: 113.00, valuation: 200e9 },
    fdvShares: 1.77e9,
    consensusFDV: 248e9,
    delta24h: +5.81,
    headline: "Colossus 2 online; 1.2M H200 cluster confirmed"
  },
  {
    id: "STRIPE",
    ticker: "STR.PRE",
    name: "Stripe",
    sector: "Fintech / Payments",
    lastRound: { date: "2025-07", price: 32.50, valuation: 91.5e9 },
    fdvShares: 2.82e9,
    consensusFDV: 118e9,
    delta24h: +0.41,
    headline: "TPV $1.6T LTM; Bridge stablecoin volume +340% QoQ"
  },
  {
    id: "ANTHROPIC",
    ticker: "ANT.PRE",
    name: "Anthropic",
    sector: "AI / Foundation Models",
    lastRound: { date: "2026-02", price: 102.00, valuation: 183e9 },
    fdvShares: 1.79e9,
    consensusFDV: 215e9,
    delta24h: +3.07,
    headline: "Series F at $183B post; Amazon $4B follow-on"
  },
  {
    id: "NEURALINK",
    ticker: "NLK.PRE",
    name: "Neuralink",
    sector: "BCI / Medical",
    lastRound: { date: "2025-06", price: 26.00, valuation: 9.0e9 },
    fdvShares: 350e6,
    consensusFDV: 13.6e9,
    delta24h: -2.20,
    headline: "Telepathy trial N=18; FDA breakthrough designation pending"
  },
  {
    id: "DATABRICKS",
    ticker: "DBX.PRE",
    name: "Databricks",
    sector: "Data / Analytics",
    lastRound: { date: "2025-12", price: 92.00, valuation: 62e9 },
    fdvShares: 680e6,
    consensusFDV: 78e9,
    delta24h: +1.24,
    headline: "ARR crosses $4B; Mosaic inference workloads 6x YoY"
  }
];

// Per-asset exchange listings.
// structure: SPV-backed | synthetic | perp | mirror-credit
// ratioSource: official | whitepaper | back-solved | market-fitted
window.LISTINGS = {
  SPACEX: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 184.72, shareRatio: 1.0000,  confidence: 92, vol24: 142.8e6, oi: 612.4e6, spread: 4 },
    { ex: "Bybit",        venue: "CEX", structure: "SPV-mirror",    ratioSource: "official",     tokenPrice: 264.10, shareRatio: 1.4300,  confidence: 84, vol24: 88.3e6,  oi: 401.0e6, spread: 7 },
    { ex: "Kraken xStock", venue: "CEX", structure: "SPV-backed",   ratioSource: "official",     tokenPrice: 184.92, shareRatio: 1.0000,  confidence: 90, vol24: 41.6e6,  oi: 188.2e6, spread: 5 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 92.55,  shareRatio: 0.5000,  confidence: 71, vol24: 67.2e6,  oi: 244.9e6, spread: 9 },
    { ex: "MEXC Pre-Mkt", venue: "CEX", structure: "synthetic",     ratioSource: "back-solved",  tokenPrice: 444.20, shareRatio: 2.4000,  confidence: 62, vol24: 53.8e6,  oi: 162.7e6, spread: 14 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 191.40, shareRatio: 1.0000,  confidence: 28, vol24: 318.4e6, oi: 892.1e6, spread: 2 },
    { ex: "Aevo",         venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 188.05, shareRatio: 1.0000,  confidence: 31, vol24: 71.9e6,  oi: 244.0e6, spread: 6 },
    { ex: "Jarsy",        venue: "DEX", structure: "mirror-credit", ratioSource: "back-solved",  tokenPrice: 187.30, shareRatio: 1.0150,  confidence: 54, vol24: 12.4e6,  oi: 38.2e6,  spread: 11 }
  ],
  OPENAI: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 233.10, shareRatio: 1.0000,  confidence: 88, vol24: 201.4e6, oi: 718.3e6, spread: 6 },
    { ex: "Bybit",        venue: "CEX", structure: "SPV-mirror",    ratioSource: "official",     tokenPrice: 332.50, shareRatio: 1.4300,  confidence: 82, vol24: 112.7e6, oi: 388.1e6, spread: 8 },
    { ex: "Kraken xStock", venue: "CEX", structure: "SPV-backed",   ratioSource: "official",     tokenPrice: 233.40, shareRatio: 1.0000,  confidence: 90, vol24: 58.2e6,  oi: 201.0e6, spread: 5 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 117.80, shareRatio: 0.5000,  confidence: 70, vol24: 91.0e6,  oi: 312.6e6, spread: 10 },
    { ex: "MEXC Pre-Mkt", venue: "CEX", structure: "synthetic",     ratioSource: "back-solved",  tokenPrice: 562.40, shareRatio: 2.4000,  confidence: 61, vol24: 47.1e6,  oi: 122.0e6, spread: 16 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 247.80, shareRatio: 1.0000,  confidence: 27, vol24: 412.6e6, oi: 1.14e9,  spread: 3 },
    { ex: "Aevo",         venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 241.20, shareRatio: 1.0000,  confidence: 30, vol24: 84.2e6,  oi: 287.4e6, spread: 7 }
  ],
  XAI: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 140.20, shareRatio: 1.0000,  confidence: 86, vol24: 88.1e6,  oi: 304.0e6, spread: 6 },
    { ex: "Bybit",        venue: "CEX", structure: "SPV-mirror",    ratioSource: "official",     tokenPrice: 199.30, shareRatio: 1.4300,  confidence: 80, vol24: 51.4e6,  oi: 188.0e6, spread: 9 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 71.80,  shareRatio: 0.5000,  confidence: 68, vol24: 32.7e6,  oi: 117.6e6, spread: 11 },
    { ex: "MEXC Pre-Mkt", venue: "CEX", structure: "synthetic",     ratioSource: "back-solved",  tokenPrice: 340.20, shareRatio: 2.4000,  confidence: 60, vol24: 28.4e6,  oi: 91.2e6,  spread: 17 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 152.70, shareRatio: 1.0000,  confidence: 29, vol24: 244.9e6, oi: 681.2e6, spread: 3 },
    { ex: "Aevo",         venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 149.10, shareRatio: 1.0000,  confidence: 32, vol24: 51.4e6,  oi: 174.3e6, spread: 7 }
  ],
  STRIPE: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 41.80,  shareRatio: 1.0000,  confidence: 91, vol24: 38.4e6,  oi: 144.2e6, spread: 4 },
    { ex: "Kraken xStock", venue: "CEX", structure: "SPV-backed",   ratioSource: "official",     tokenPrice: 41.92, shareRatio: 1.0000,  confidence: 90, vol24: 22.8e6,  oi: 88.0e6,  spread: 5 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 20.95,  shareRatio: 0.5000,  confidence: 72, vol24: 18.1e6,  oi: 71.0e6,  spread: 8 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 43.10,  shareRatio: 1.0000,  confidence: 30, vol24: 91.6e6,  oi: 244.0e6, spread: 3 },
    { ex: "Aevo",         venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 42.50,  shareRatio: 1.0000,  confidence: 33, vol24: 27.1e6,  oi: 88.2e6,  spread: 6 }
  ],
  ANTHROPIC: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 119.80, shareRatio: 1.0000,  confidence: 89, vol24: 71.4e6,  oi: 244.8e6, spread: 5 },
    { ex: "Bybit",        venue: "CEX", structure: "SPV-mirror",    ratioSource: "official",     tokenPrice: 171.10, shareRatio: 1.4300,  confidence: 81, vol24: 44.2e6,  oi: 162.1e6, spread: 8 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 60.20,  shareRatio: 0.5000,  confidence: 70, vol24: 29.6e6,  oi: 101.4e6, spread: 9 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 124.50, shareRatio: 1.0000,  confidence: 28, vol24: 188.4e6, oi: 502.0e6, spread: 3 }
  ],
  NEURALINK: [
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 19.40,  shareRatio: 0.5000,  confidence: 64, vol24: 8.2e6,   oi: 28.4e6,  spread: 12 },
    { ex: "MEXC Pre-Mkt", venue: "CEX", structure: "synthetic",     ratioSource: "back-solved",  tokenPrice: 93.60,  shareRatio: 2.4000,  confidence: 55, vol24: 4.1e6,   oi: 14.0e6,  spread: 22 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 41.20,  shareRatio: 1.0000,  confidence: 24, vol24: 51.7e6,  oi: 144.0e6, spread: 4 },
    { ex: "Aevo",         venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 38.90,  shareRatio: 1.0000,  confidence: 27, vol24: 18.4e6,  oi: 54.2e6,  spread: 9 }
  ],
  DATABRICKS: [
    { ex: "Binance",      venue: "CEX", structure: "SPV-backed",    ratioSource: "official",     tokenPrice: 113.40, shareRatio: 1.0000,  confidence: 87, vol24: 18.2e6,  oi: 64.1e6,  spread: 6 },
    { ex: "Kraken xStock", venue: "CEX", structure: "SPV-backed",   ratioSource: "official",     tokenPrice: 113.80, shareRatio: 1.0000,  confidence: 89, vol24: 12.4e6,  oi: 41.2e6,  spread: 5 },
    { ex: "Bitget",       venue: "CEX", structure: "synthetic",     ratioSource: "whitepaper",   tokenPrice: 56.50,  shareRatio: 0.5000,  confidence: 71, vol24: 9.7e6,   oi: 31.8e6,  spread: 9 },
    { ex: "Hyperliquid",  venue: "DEX", structure: "perp",          ratioSource: "market-fitted",tokenPrice: 117.20, shareRatio: 1.0000,  confidence: 29, vol24: 42.1e6,  oi: 112.4e6, spread: 4 }
  ]
};

// generate 96 points of intraday "implied FDV" series per exchange
window.SERIES = (function() {
  const out = {};
  Object.keys(window.LISTINGS).forEach(aid => {
    const asset = window.ASSETS.find(a => a.id === aid);
    out[aid] = {};
    window.LISTINGS[aid].forEach((row, idx) => {
      const baseFDV = (row.tokenPrice / row.shareRatio) * asset.fdvShares;
      // deterministic pseudo-random walk per exchange
      let seed = (aid.charCodeAt(0) * 7 + idx * 131) % 997;
      const rand = () => {
        seed = (seed * 9301 + 49297) % 233280;
        return seed / 233280;
      };
      const pts = [];
      // larger vol for perps, smaller for SPV
      const vol = row.structure === "perp" ? 0.018
                : row.structure === "synthetic" ? 0.011
                : row.structure === "mirror-credit" ? 0.014
                : 0.006;
      let v = baseFDV * (1 - vol * 4);
      for (let i = 0; i < 96; i++) {
        v = v + (baseFDV - v) * 0.05 + (rand() - 0.5) * baseFDV * vol;
        pts.push(v);
      }
      // anchor last point exactly at current baseFDV
      pts[pts.length - 1] = baseFDV;
      out[aid][row.ex] = pts;
    });
  });
  return out;
})();
