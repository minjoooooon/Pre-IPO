// Verify AssetHeader hero displays the right unit (B vs T) for each asset.

import { createRequire } from "module";
const require = createRequire(import.meta.url);
const puppeteer = require("C:/Users/user/AppData/Local/Temp/jsx-check/node_modules/puppeteer");

const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
const page = await browser.newPage();
await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle2" });

await page.waitForFunction(
  () => window.PRE_IPO_LIVE && window.PRE_IPO_LIVE.lastTickAt != null,
  { timeout: 20000 }
);

// Read hero for each asset by clicking the rail row
const assetIds = ["SPACEX", "OPENAI", "ANTHROPIC"];
const out = {};
for (const id of assetIds) {
  const clicked = await page.evaluate((id) => {
    const rows = document.querySelectorAll(".rail-row");
    for (const r of rows) {
      const tick = r.querySelector(".rail-ticker");
      // map id → ticker prefix
      const map = { SPACEX: "SPX", OPENAI: "OAI", ANTHROPIC: "ANT" };
      if (tick && tick.innerText.startsWith(map[id])) {
        r.click();
        return true;
      }
    }
    return false;
  }, id);
  if (!clicked) {
    out[id] = "rail row not found";
    continue;
  }
  await new Promise((r) => setTimeout(r, 300));
  const hero = await page.evaluate(() => {
    const num = document.querySelector(".hero-num")?.innerText || "";
    const unit = document.querySelector(".hero-unit")?.innerText || "";
    const consensus = (window.ASSETS || []).find((a) => a.id ===
      (document.querySelector(".asset-ticker")?.innerText || "").split(".")[0]
    );
    return { display: `$${num} ${unit}`.trim(), unitSuffix: unit };
  });
  out[id] = hero;
}

console.log(JSON.stringify(out, null, 2));
await browser.close();
