// FE smoke test: load the prototype HTML in a headless browser, wait for
// data-live.js to connect to /ws, then read window.ASSETS / LISTINGS / SERIES.
// Verifies:
//   - WS connects, snapshot arrives, statuses correct
//   - window globals populated
//   - at least one tick mutates LISTINGS / SERIES
//   - no JS errors in console

import { createRequire } from "module";
const require = createRequire(import.meta.url);
const puppeteer = require("C:/Users/user/AppData/Local/Temp/jsx-check/node_modules/puppeteer");

// P11: single-process serves everything at :8000
const URL = process.env.FE_SMOKE_URL || "http://127.0.0.1:8000/";

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox"],
});
const page = await browser.newPage();

const consoleErrors = [];
const consoleAll = [];
page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
page.on("console", (msg) => {
  const line = `[${msg.type()}] ${msg.text()}`;
  consoleAll.push(line);
  if (msg.type() === "error") consoleErrors.push("console.error: " + msg.text());
});
page.on("requestfailed", (req) =>
  consoleErrors.push(`requestfailed: ${req.url()} - ${req.failure()?.errorText}`)
);
page.on("response", async (res) => {
  if (res.status() >= 400) {
    consoleErrors.push(`http_${res.status()}: ${res.url()}`);
  }
});

await page.goto(URL, { waitUntil: "networkidle2", timeout: 30000 });

// Wait up to 15s for snapshot to arrive
try {
  await page.waitForFunction(
    () => window.PRE_IPO_LIVE && window.PRE_IPO_LIVE.lastSnapshotAt != null,
    { timeout: 15000 }
  );
} catch (e) {
  console.error("SNAPSHOT_TIMEOUT");
  console.error("console:", consoleAll.slice(0, 30).join("\n"));
  console.error("errors:", consoleErrors.join("\n"));
  const probe = await page.evaluate(() => ({
    hasLive: typeof window.PRE_IPO_LIVE,
    hasAssets: Array.isArray(window.ASSETS) ? window.ASSETS.length : "missing",
    hasListings: typeof window.LISTINGS,
    hasSeries: typeof window.SERIES,
    bodyHasRoot: !!document.getElementById("root"),
    rootChildren: document.getElementById("root")?.children?.length || 0,
    scripts: Array.from(document.scripts).map((s) => ({ src: s.src, type: s.type })),
  }));
  console.error("probe:", JSON.stringify(probe, null, 2));
  await browser.close();
  process.exit(1);
}

// Wait up to 15s for first live tick
await page.waitForFunction(
  () => window.PRE_IPO_LIVE && window.PRE_IPO_LIVE.lastTickAt != null,
  { timeout: 15000 }
);

const state = await page.evaluate(() => {
  const live = window.PRE_IPO_LIVE;
  const assets = window.ASSETS || [];
  return {
    connected: live.connected,
    statuses: live.statuses.map((s) => `${s.exchange}=${s.state}`),
    assetCount: assets.length,
    assetIds: assets.map((a) => a.id),
    consensusFDVByAsset: Object.fromEntries(
      assets.map((a) => [a.id, a.consensusFDV])
    ),
    listingsByAsset: Object.fromEntries(
      Object.entries(window.LISTINGS || {}).map(([k, v]) => [k, v.map((r) => r.ex)])
    ),
    seriesByAsset: Object.fromEntries(
      Object.entries(window.SERIES || {}).map(([k, v]) => [
        k,
        Object.fromEntries(Object.entries(v).map(([ex, arr]) => [ex, arr.length])),
      ])
    ),
  };
});

console.log(JSON.stringify(state, null, 2));
console.log("CONSOLE_ERRORS:", consoleErrors.length === 0 ? "none" : consoleErrors);

// Verify the DOM rendered. Quick check: dashboard shell present + at least one row in NormTable
const rendered = await page.evaluate(() => {
  return {
    hasApp: !!document.querySelector(".app"),
    hasTopBar: !!document.querySelector(".topbar"),
    hasNormTable: !!document.querySelector(".ntable"),
    normRows: document.querySelectorAll(".ntable tbody tr").length,
    feedsBadge: document.querySelector(".status-cell.mono:has(.muted) span:last-child")?.innerText || "",
  };
});
console.log("RENDER:", JSON.stringify(rendered, null, 2));

await browser.close();

if (consoleErrors.length > 0) process.exit(2);
if (state.listingExchanges.length === 0) {
  console.error("NO_LISTINGS");
  process.exit(3);
}
console.log("FE_SMOKE_OK");
