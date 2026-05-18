// Verify D changes: hero premium badge + NormTable vs REF column
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

const map = { SPACEX: "SPX", OPENAI: "OAI", ANTHROPIC: "ANT" };
const results = {};
for (const id of Object.keys(map)) {
  await page.evaluate((id, prefix) => {
    document.querySelectorAll(".rail-row").forEach((r) => {
      const t = r.querySelector(".rail-ticker");
      if (t && t.innerText.startsWith(prefix)) r.click();
    });
  }, id, map[id]);
  await new Promise((r) => setTimeout(r, 300));
  const data = await page.evaluate(() => {
    const heroNum = document.querySelector(".hero-num")?.innerText || "";
    const heroUnit = document.querySelector(".hero-unit")?.innerText || "";
    const heroPrem = document.querySelector(".hero-prem")?.innerText || "";
    const heroSub = document.querySelector(".hero-sub")?.innerText.replace(/\s+/g, " ") || "";
    const headers = Array.from(document.querySelectorAll(".ntable thead th")).map((th) => th.innerText);
    const firstRow = Array.from(
      document.querySelectorAll(".ntable tbody tr:first-child td")
    ).map((td) => td.innerText);
    return {
      hero: `$${heroNum} ${heroUnit}   ${heroPrem}`,
      heroSub,
      ntableHeaders: headers,
      firstRow,
    };
  });
  results[id] = data;
}

console.log(JSON.stringify(results, null, 2));
await browser.close();
