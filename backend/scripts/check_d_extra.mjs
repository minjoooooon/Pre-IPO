// Verify D-extra: AssetRail premium, disabled rows in NormTable, source links
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

// Select SPACEX
await page.evaluate(() => {
  document.querySelectorAll(".rail-row").forEach((r) => {
    const t = r.querySelector(".rail-ticker");
    if (t && t.innerText.startsWith("SPX")) r.click();
  });
});
await new Promise((r) => setTimeout(r, 400));

const data = await page.evaluate(() => {
  // AssetRail premium text per row
  const railRows = Array.from(document.querySelectorAll(".rail-row")).map((r) => ({
    ticker: r.querySelector(".rail-ticker")?.innerText,
    bottomText: r.querySelector(".rail-row-bot")?.innerText.replace(/\s+/g, " "),
  }));

  // Disabled row in NormTable
  const disRow = document.querySelector(".ntable-row-disabled");
  const disData = disRow
    ? {
        venue: disRow.querySelector(".venue-name")?.innerText,
        badge: disRow.querySelector(".dis-badge")?.innerText.replace(/\s+/g, " "),
        link: disRow.querySelector(".dis-link")?.href,
      }
    : null;

  // Source link in shares editor
  const sharesLink = document.querySelector(".fdv-editor .src-link");
  const sharesLinkUrl = sharesLink ? sharesLink.href : null;

  // Source link in any ratio source cell
  const ratioLinks = Array.from(document.querySelectorAll(".ntable .src-link")).map(
    (a) => a.href
  );

  // window.DISABLED state
  const disabledState = window.DISABLED;

  return { railRows, disRow: disData, sharesLinkUrl, ratioLinks, disabledState };
});

console.log(JSON.stringify(data, null, 2));
await browser.close();
