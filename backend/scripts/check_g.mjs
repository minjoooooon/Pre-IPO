// Verify G: alert button, tick age, alert toast end-to-end
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const puppeteer = require("C:/Users/user/AppData/Local/Temp/jsx-check/node_modules/puppeteer");

const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
const page = await browser.newPage();

// Reset any persisted alerts before load
await page.evaluateOnNewDocument(() => {
  try { localStorage.removeItem("preIpoAlerts"); } catch (e) {}
});

await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle2" });
await page.waitForFunction(
  () => window.PRE_IPO_LIVE && window.PRE_IPO_LIVE.lastTickAt != null,
  { timeout: 20000 }
);
await new Promise((r) => setTimeout(r, 500));

const baseState = await page.evaluate(() => ({
  hasAlertButton: !!document.querySelector(".alert-btn"),
  alertBtnText: document.querySelector(".alert-btn")?.innerText,
  tickAgeCount: document.querySelectorAll(".ntable .tick-age").length,
  tickAges: Array.from(document.querySelectorAll(".ntable .tick-age")).map((t) => t.innerText),
}));
console.log("base state:", JSON.stringify(baseState, null, 2));

// Programmatically add an always-fires alert (premium-above 0)
const added = await page.evaluate(() => {
  return window.PRE_IPO_ALERTS.add({
    assetId: "SPACEX",
    exchange: "consensus",
    kind: "premium-above",
    value: 0,
  });
});
console.log("rule added:", JSON.stringify(added));

// Manually fire the event since the threshold may already be exceeded (premium > 0)
// — the alert engine only fires on CROSSING. We dispatch directly to validate the UI.
await page.evaluate(() => {
  window.dispatchEvent(
    new CustomEvent("pre-ipo:alert", {
      detail: {
        rule: { kind: "premium-above", assetId: "SPACEX", value: 0 },
        payload: { message: "TEST: SPACEX consensus premium crossed +0%" },
        at: Date.now(),
      },
    })
  );
});
await new Promise((r) => setTimeout(r, 500));

const toastState = await page.evaluate(() => ({
  toastCount: document.querySelectorAll(".alert-toast").length,
  toasts: Array.from(document.querySelectorAll(".alert-toast")).map((t) => ({
    kind: t.querySelector(".alert-toast-kind")?.innerText,
    msg: t.querySelector(".alert-toast-msg")?.innerText,
  })),
}));
console.log("toast state:", JSON.stringify(toastState, null, 2));

// Click the close button on the toast
await page.evaluate(() => {
  document.querySelector(".alert-toast-x")?.click();
});
await new Promise((r) => setTimeout(r, 200));

const afterClose = await page.evaluate(() => ({
  toastCount: document.querySelectorAll(".alert-toast").length,
}));
console.log("after close:", JSON.stringify(afterClose));

await browser.close();
