/**
 * End-to-end test against the two throwaway When2meet test events (live site!):
 * real Chrome + the built extension; uploads the fixture in the side panel, reads it
 * via the local service, fills (specific dates: Fill button; days of week: skip-review),
 * checks the saved availability and outlines, then undoes and checks the restore.
 *
 * Needs: the service running on 127.0.0.1:8765 and `npm run build`.
 * Usage: npm run e2e   (or: node e2e/fill.e2e.mjs [dist-dir] [fixture.png])
 * Env: CHROME_PATH to override the Chrome executable.
 */
import puppeteer from "puppeteer-core";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = resolve(process.argv[2] ?? join(HERE, "..", "dist"));
const FIXTURE = resolve(process.argv[3] ?? join(HERE, "..", "..", "fixtures", "ios_calendar_mon_tue.png"));
const USER = "simon-claude";
const EVENTS = {
  specific: "https://www.when2meet.com/?39090990-pwxVg",
  dow: "https://www.when2meet.com/?39090991-uAXKc",
};

/**
 * Read the server's saved availability for a person from a fresh page load.
 * @param {string} url Event URL.
 * @param {number} personId Person ID.
 * @returns {Promise<{all: number[], mine: Set<number>}>} All slots and the person's available slots.
 */
async function serverState(url, personId) {
  const html = await (await fetch(url)).text();
  const ts = [...html.matchAll(/TimeOfSlot\[(\d+)\]=(\d+);/g)].map((m) => Number(m[2]));
  const mine = new Set();
  for (const m of html.matchAll(/AvailableAtSlot\[(\d+)\]\.push\((\d+)\)/g)) {
    if (Number(m[2]) === personId) mine.add(ts[Number(m[1])]);
  }
  return { all: ts, mine };
}

/**
 * Wall-clock info of a slot: specific dates -> New York time; days-of-week -> UTC-encoded wall clock.
 * @param {number} ts Slot timestamp.
 * @param {boolean} dow Days-of-week mode.
 * @returns {{day: string, hm: string}} Weekday and HH:MM.
 */
function wall(ts, dow) {
  const opts = { timeZone: dow ? "UTC" : "America/New_York", hourCycle: "h23" };
  const d = new Date(ts * 1000);
  const day = d.toLocaleDateString("en-US", { ...opts, weekday: "short" });
  const hm = d.toLocaleTimeString("en-US", { ...opts, hour: "2-digit", minute: "2-digit" });
  return { day, hm };
}

const BUSY = {
  Mon: [["09:00", "09:15"], ["09:30", "12:15"], ["14:00", "15:00"]],
  Tue: [["09:00", "09:15"], ["09:30", "10:45"], ["14:00", "15:15"], ["15:30", "16:45"]],
};
const LOW = { Mon: ["09:00", "14:15"], Tue: ["09:00"] };

/**
 * Expected availability after filling the fixture sample ("Plan out Tomorrow" not busy).
 * @param {number[]} all All slot timestamps.
 * @param {boolean} dow Days-of-week mode.
 * @returns {Set<number>} Expected available slots.
 */
function expectedAvailable(all, dow) {
  return new Set(
    all.filter((ts) => {
      const { day, hm } = wall(ts, dow);
      return !(BUSY[day] ?? []).some(([a, b]) => hm >= a && hm < b);
    }),
  );
}

/**
 * Compare two sets and describe differences.
 * @param {Set<number>} got Actual.
 * @param {Set<number>} want Expected.
 * @param {boolean} dow Days-of-week mode (for printing).
 * @returns {string} "" if equal, otherwise a description.
 */
function diff(got, want, dow) {
  const fmt = (ts) => `${wall(ts, dow).day} ${wall(ts, dow).hm}`;
  const extra = [...got].filter((t) => !want.has(t)).map(fmt);
  const missing = [...want].filter((t) => !got.has(t)).map(fmt);
  return extra.length || missing.length ? `extra=[${extra.join(", ")}] missing=[${missing.join(", ")}]` : "";
}

/**
 * Click the side panel button and wait for the status message to change.
 * @param {import("puppeteer-core").Page} panel Side panel page.
 * @param {string} selector Button selector.
 * @returns {Promise<string>} The final message.
 */
async function clickAndWait(panel, selector) {
  await panel.$eval("#message", (el) => (el.textContent = ""));
  await panel.$eval(selector, (el) => el.click());
  await panel.waitForFunction(
    () => {
      const t = document.getElementById("message").textContent;
      return t && !t.endsWith("…");
    },
    { timeout: 180000 },
  );
  return panel.$eval("#message", (el) => el.textContent);
}

/**
 * Run the fill/undo test on one event.
 * @param {import("puppeteer-core").Browser} browser Browser.
 * @param {string} extId Extension ID.
 * @param {string} url Event URL.
 * @param {boolean} dow Days-of-week mode.
 * @returns {Promise<boolean>} Whether all checks passed.
 */
async function runEvent(browser, extId, url, dow) {
  let ok = true;
  const check = (cond, label) => {
    console.log(`  ${cond ? "PASS" : "FAIL"} ${label}`);
    ok &&= cond;
  };
  console.log(`\n== ${dow ? "days of week" : "specific dates"}: ${url}`);
  const w2m = await browser.newPage();
  await w2m.setViewport({ width: 1400, height: 1600 });
  await w2m.goto(url, { waitUntil: "load" });
  await w2m.evaluate((name) => {
    document.getElementById("name").value = name;
    ProcessLogin();
  }, USER);
  await w2m.waitForFunction(() => UserID && document.getElementById("YouGrid").style.display !== "none");
  const personId = await w2m.evaluate(() => UserID);

  // Pre-state: real mouse drag on the 3rd column, rows 8..15, so undo has something to restore.
  const pre = await w2m.evaluate(() => {
    const cells = [...document.querySelectorAll("[id^=YouTime]")].filter((c) => c.dataset.col === "4");
    return [cells.find((c) => c.dataset.row === "8").id, cells.find((c) => c.dataset.row === "15").id];
  });
  const start = await (await w2m.$(`#${pre[0]}`)).boundingBox();
  const end = await (await w2m.$(`#${pre[1]}`)).boundingBox();
  const before = await serverState(url, personId);
  const wasAvailable = before.mine.has(Number(pre[0].slice(7)));
  await w2m.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await w2m.mouse.down();
  await w2m.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 5 });
  await w2m.mouse.up();
  await new Promise((r) => setTimeout(r, 1500));
  const preState = await serverState(url, personId);
  console.log(`  pre-state: ${preState.mine.size} available slots (real drag ${wasAvailable ? "cleared" : "set"} col 4 rows 8-15)`);

  // Side panel as a tab; point its "active tab" lookup at the When2meet tab.
  const panel = await browser.newPage();
  await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
  await panel.evaluate((u) => {
    const real = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = async () => real({ url: u.split("?")[0] + "*" });
  }, url);
  await panel.$eval("#refresh", (el) => el.click());
  await panel.waitForFunction(() => document.getElementById("page-status").textContent.includes("simon-claude"), {
    timeout: 20000,
  });
  console.log("  status:", await panel.$eval("#page-status", (e) => e.textContent));
  check(
    (await panel.$eval("#page-status", (e) => e.textContent)).includes(dow ? "days of week" : "specific dates"),
    "mode detected",
  );

  if (dow) {
    // Skip-review path: turn the setting on, then reading the screenshot fills immediately.
    await panel.evaluate(async () => {
      const s = (await chrome.storage.local.get("settings")).settings ?? {};
      await chrome.storage.local.set({ settings: { ...s, skipReview: true } });
    });
    await panel.reload();
    await panel.evaluate((u) => {
      const real = chrome.tabs.query.bind(chrome.tabs);
      chrome.tabs.query = async () => real({ url: u.split("?")[0] + "*" });
    }, url);
  }
  const input = await panel.$("#files");
  await input.uploadFile(FIXTURE);
  const t0 = Date.now();
  const readMsg = await clickAndWait(panel, "#extract");
  console.log(`  read (${((Date.now() - t0) / 1000).toFixed(1)}s): ${readMsg.replaceAll("\n", " | ")}`);
  check(dow || readMsg.startsWith("Found 12 events, 5 to double-check"), "extraction found 12 events, 5 low");
  const rows = await panel.$$eval(".event", (els) => els.length);
  check(rows === 12, `review shows 12 rows (${rows})`);
  const notBusy = await panel.$$eval(".event.not-busy .title", (els) => els.map((e) => e.textContent));
  check(notBusy.length === 2 && notBusy.every((t) => t === "Plan out Tomorrow"), "keyword rule unchecked 'Plan out Tomorrow'");
  await panel.$eval("#show-debug", (el) => el.click());
  const debugSrc = await panel.$eval("#images img", (img) => img.src.slice(0, 22));
  check(debugSrc === "data:image/png;base64,", "debug overlay toggle shows the overlay");
  if (!dow) {
    await panel.setViewport({ width: 420, height: 1800 });
    await panel.screenshot({ path: join(tmpdir(), "s2m-review-panel.png"), fullPage: true });
  }
  const fillMsg = dow
    ? (await panel.waitForFunction(() => document.getElementById("message").textContent.includes("Done:"), { timeout: 120000 }),
      await panel.$eval("#message", (el) => el.textContent))
    : await clickAndWait(panel, "#fill");
  console.log(`  fill (${((Date.now() - t0) / 1000).toFixed(1)}s): ${fillMsg.replace(/\n/g, " | ")}`);
  check(fillMsg.includes("Done:"), "fill reported success");

  const afterFill = await serverState(url, personId);
  const want = expectedAvailable(afterFill.all, dow);
  const d1 = diff(afterFill.mine, want, dow);
  check(!d1, `server availability matches expected after fill ${d1}`);

  const outlined = await w2m.$$eval(".s2m-lowconf", (els) => els.map((e) => Number(e.id.slice(7))));
  const outlinedWall = outlined.map((ts) => `${wall(ts, dow).day} ${wall(ts, dow).hm}`).sort();
  const wantLow = Object.entries(LOW).flatMap(([d, hs]) => hs.map((h) => `${d} ${h}`)).sort();
  check(JSON.stringify(outlinedWall) === JSON.stringify(wantLow), `outlines = ${outlinedWall.join(", ")}`);

  // Outlines must not block clicking: the topmost element at an outlined cell's center is the cell.
  const hit = await w2m.evaluate(() => {
    const el = document.querySelector(".s2m-lowconf");
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === el;
  });
  check(hit, "outlined cell is still the click target");

  const undoMsg = await clickAndWait(panel, "#undo");
  console.log(`  undo: ${undoMsg}`);
  const afterUndo = await serverState(url, personId);
  const d2 = diff(afterUndo.mine, preState.mine, dow);
  check(!d2, `server availability restored by undo ${d2}`);
  check((await w2m.$$(".s2m-lowconf")).length === 0, "outlines cleared by undo");

  await panel.close();
  await w2m.close();
  return ok;
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
  enableExtensions: true,
  pipe: true,
  userDataDir: mkdtempSync(join(tmpdir(), "s2m-e2e-")),
});
const extId = await browser.installExtension(DIST);
console.log("extension id:", extId);
let allOk = true;
allOk = (await runEvent(browser, extId, EVENTS.specific, false)) && allOk;
allOk = (await runEvent(browser, extId, EVENTS.dow, true)) && allOk;
await browser.close();
console.log(allOk ? "\nALL PASSED" : "\nSOME CHECKS FAILED");
process.exit(allOk ? 0 : 1);
