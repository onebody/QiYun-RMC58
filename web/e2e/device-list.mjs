// End-to-end check of the DeviceList dialog against a mocked /api/endpoints.
// Uses puppeteer-core (already a dev dependency). Spins up vite dev server
// with WIDSCAN_E2E_MOCK=1 to inject the mock, then opens the page headlessly
// and drives the dialog through its states.
//
//   node e2e/device-list.mjs [--url http://localhost:5173/] [--out dir]
//                             [--chrome /path/to/chrome]  # or CHROME_BIN env
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import {
  parseCliArgs,
  resolveChromeExecutable,
  formatChromeNotFound,
  HELP_TEXT,
} from "./chrome-resolver.mjs";

// ----- CLI arg parsing -----------------------------------------------------

const { values, unknown, help } = parseCliArgs(process.argv.slice(2), {
  extraKnown: { "--url": "string", "--out": "string", "--port": "string" },
});
if (help) {
  console.log(HELP_TEXT);
  process.exit(0);
}
if (unknown.length > 0) {
  console.error(`Unknown flags: ${unknown.join(", ")}\nRun with --help for usage.`);
  process.exit(2);
}

const base     = values["--url"] ?? null;
const outDir   = values["--out"] ?? "e2e-out";
const port     = values["--port"] ?? "5198";

// ----- Chrome resolution ---------------------------------------------------

const resolved = resolveChromeExecutable({ cliPath: values["--chrome"] });
if (resolved.source === "none") {
  console.error(formatChromeNotFound(resolved));
  console.error(`\nReason: ${resolved.error}`);
  process.exit(1);
}
console.log(`[device-list.e2e] Using Chrome from ${resolved.source}: ${resolved.path}`);
if (resolved.version) console.log(`[device-list.e2e] ${resolved.version}`);

fs.mkdirSync(outDir, { recursive: true });

// ----- Helpers (defined early so vite-spawn block below can use them) ----
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};

// ----- Vite dev server -----------------------------------------------------
//
// We do NOT spawn vite from inside this Node script — on macOS + node:child_process
// the vite dev server reliably reaches its "ready in 200ms" log but then
// immediately becomes unreachable on localhost (spawned npx/vite exits before
// the listener binds, despite the log message). The reliable pattern is to
// start vite from the shell (background & sleep), then point this script at
// it with --url. The npm run script below does exactly that:
//
//   "e2e:device-list": "bash -c 'WIDSCAN_E2E_MOCK=1 npx vite --port 5198 & ...'"
//
// Running this script directly? Start vite manually then pass --url:
//   WIDSCAN_E2E_MOCK=1 npx vite --port 5198 &
//   node e2e/device-list.mjs --url http://localhost:5198/

const url = base;
if (!url) {
  console.error(
    "device-list.mjs needs a running vite dev server. Run via:\n" +
    "  npm run e2e:device-list\n" +
    "or manually:\n" +
    "  WIDSCAN_E2E_MOCK=1 npx vite --port 5198 &\n" +
    "  node e2e/device-list.mjs --url http://localhost:5198/",
  );
  process.exit(2);
}

// ----- Launch browser ------------------------------------------------------

const browser = await puppeteer.launch({
  executablePath: resolved.path,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(`[console] ${m.text()}`);
});
page.on("pageerror", (e) => consoleErrors.push(`[pageerror] ${String(e)}`));

try {
  // ----- Open page --------------------------------------------------------
  await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
  await sleep(800);
  await page.screenshot({ path: path.join(outDir, "dl-00-landing.png"), fullPage: true });

  // Sanity: mock endpoint reachable from the page
  const endpointsResp = await page.evaluate(async () => {
    try {
      const r = await fetch("/api/endpoints", { cache: "no-store" });
      const j = await r.json();
      return { ok: r.ok, count: j?.endpoints?.length ?? null, ids: j?.endpoints?.map((e) => e.id) ?? [] };
    } catch (e) { return { ok: false, err: String(e) }; }
  });
  check("mock /api/endpoints reachable", endpointsResp.ok && endpointsResp.count === 5,
    JSON.stringify(endpointsResp));

  // Dialog must be closed initially (no showModal() called yet)
  const initialOpen = await page.evaluate(() => {
    const d = document.querySelector("dialog.device-list");
    if (!d) return { found: false };
    return { found: true, open: d.hasAttribute("open") };
  });
  check("dialog not shown before click", initialOpen.found && !initialOpen.open,
    JSON.stringify(initialOpen));

  // ----- Open dialog via "Device management" button -----------------------
  const openBtn = await page.$("button");
  // The device management button text is in the header actions area
  const openDeviceMgrBtn = (await page.$$("button")).find(async (b) =>
    ((await b.evaluate((el) => el.textContent)) || "").includes("Device management") ||
    ((await b.evaluate((el) => el.title)) || "").includes("Device management")
  );
  // Simpler fallback: click the element with "Device management" text anywhere
  await page.evaluate(() => {
    const btns = document.querySelectorAll("button");
    for (const b of btns) {
      if (b.textContent?.includes("Device management") || b.title?.includes("Device management")) {
        b.click();
        return;
      }
    }
  });
  await sleep(500); // DeviceManager fetches every 2s in real code; our mock responds immediately so 500ms is plenty

  // The dialog should now have open attribute and be visible
  const dialogState = await page.evaluate(() => {
    const d = document.querySelector("dialog.device-list");
    if (!d) return { found: false };
    const rect = d.getBoundingClientRect();
    const style = getComputedStyle(d);
    return {
      found: true,
      open: d.hasAttribute("open"),
      display: style.display,
      hiddenAttr: d.hasAttribute("hidden"),
      width: Math.round(rect.width),
      title: d.querySelector(".device-list__title")?.textContent ?? null,
      countText: d.querySelector(".device-list__count")?.textContent ?? null,
      sectionTitles: [...d.querySelectorAll(".device-section__title")].map((el) => el.textContent?.trim()),
      cards: [...d.querySelectorAll(".device-card")].length,
      firstTransport: [...d.querySelectorAll(".device-section__title")][0]?.textContent?.trim() ?? null,
      lastTransport: [...d.querySelectorAll(".device-section__title")].at(-1)?.textContent?.trim() ?? null,
    };
  });
  await page.screenshot({ path: path.join(outDir, "dl-01-dialog-open.png"), fullPage: true });

  check("dialog has open attribute", dialogState.open, JSON.stringify(dialogState));
  check("dialog title = 设备管理", dialogState.title === "设备管理", JSON.stringify(dialogState));
  check("count label mentions 5 devices",
    /5\s*个设备可连接/.test(dialogState.countText ?? ""), dialogState.countText);
  check("WiFi section shown first", /WiFi|wifi/.test(dialogState.firstTransport ?? ""),
    dialogState.firstTransport);
  check("USB section present",
    dialogState.sectionTitles?.some((s) => /USB/.test(s ?? "")) ?? false,
    JSON.stringify(dialogState.sectionTitles));
  check("5 cards rendered", dialogState.cards === 5, `cards=${dialogState.cards}`);

  // ----- Detailed card structure check ------------------------------------
  const cardDetails = await page.evaluate(() => {
    return [...document.querySelectorAll(".device-card")].map((card) => {
      const id = card.getAttribute("data-device-id");
      const classes = card.className;
      const name = card.querySelector(".device-card__name")?.textContent?.trim() ?? "";
      const kindTag = card.querySelector(".device-card__kind")?.textContent?.trim() ?? "";
      const subline = card.querySelector(".device-card__subline")?.textContent?.trim() ?? "";
      const statusDot = card.querySelector(".device-card__status")?.className ?? "";
      const statusTitle = card.querySelector(".device-card__status")?.getAttribute("title") ?? "";
      const iconText = card.querySelector(".device-card__icon")?.textContent?.trim() ?? "";
      const connectBtn = card.querySelector(".device-card__btn--connect");
      const manageBtn = card.querySelector(".device-card__btn--manage");
      return {
        id, classes, name, kindTag, subline,
        statusDot, statusTitle, iconText,
        connectDisabled: connectBtn?.disabled,
        manageDisabled: manageBtn?.disabled,
        connectTitle: connectBtn?.getAttribute("title") ?? "",
        manageTitle: manageBtn?.getAttribute("title") ?? "",
      };
    });
  });

  // WiFi relay (widscan58-52a1d8, online, not busy)
  const relay = cardDetails.find((c) => c.id === "node:widscan58-52a1d8");
  check("relay card exists", !!relay);
  check("relay has online dot", relay?.statusDot.includes("online") && !relay?.statusDot.includes("busy"),
    JSON.stringify(relay));
  check("relay kindTag = Relay", relay?.kindTag === "Relay", JSON.stringify(relay));
  check("relay icon = 📶", relay?.iconText === "📶", JSON.stringify(relay));
  check("relay connect enabled", relay?.connectDisabled === false, JSON.stringify(relay));
  check("relay manage enabled", relay?.manageDisabled === false, JSON.stringify(relay));

  // Busy relay
  const busy = cardDetails.find((c) => c.id === "node:widscan58-99ffee");
  check("busy relay exists", !!busy);
  check("busy dot + card--busy", busy?.statusDot.includes("busy") && busy?.classes.includes("device-card--busy"),
    JSON.stringify(busy));
  check("busy connect disabled", busy?.connectDisabled === true, JSON.stringify(busy));
  check("busy manage still enabled", busy?.manageDisabled === false, JSON.stringify(busy));

  // Online serial
  const serialOk = cardDetails.find((c) => c.id === "serial:/dev/cu.usbmodem1301");
  check("online serial exists", !!serialOk);
  check("online serial dot", serialOk?.statusDot.includes("online") && !serialOk?.statusDot.includes("offline"),
    JSON.stringify(serialOk));
  check("online serial kindTag = Scanner", serialOk?.kindTag === "Scanner", JSON.stringify(serialOk));
  check("online serial subline = path", serialOk?.subline === "/dev/cu.usbmodem1301",
    JSON.stringify(serialOk));
  check("online serial connect enabled", serialOk?.connectDisabled === false, JSON.stringify(serialOk));
  check("online serial manage disabled (not remote)", serialOk?.manageDisabled === true, JSON.stringify(serialOk));
  check("online serial manage title mentions WiFi relay",
    /WiFi relay/.test(serialOk?.manageTitle ?? ""), JSON.stringify(serialOk));

  // Offline serial
  const offline = cardDetails.find((c) => c.id === "serial:/dev/cu.usbmodem1201");
  check("offline serial exists", !!offline);
  check("offline dot", offline?.statusDot.includes("offline"), JSON.stringify(offline));
  check("offline card--offline class", offline?.classes.includes("device-card--offline"),
    JSON.stringify(offline));
  check("offline connect disabled", offline?.connectDisabled === true, JSON.stringify(offline));
  check("offline connect title mentions 离线",
    /离线/.test(offline?.connectTitle ?? ""), JSON.stringify(offline));

  // UART (kind=uart, should also have manage disabled since not remote)
  const uart = cardDetails.find((c) => c.id === "uart:/dev/serial0");
  check("uart exists", !!uart);
  check("uart manage disabled", uart?.manageDisabled === true, JSON.stringify(uart));

  // ----- Section ordering (wifi → usb) ------------------------------------
  // Count cards per section. The UART card lives under the USB section because
  // DeviceList groups by transport, not by kind (transportOf("uart") = "usb").
  const sectionGroups = await page.evaluate(() => {
    const sections = [...document.querySelectorAll(".device-section")];
    return sections.map((s) => {
      const title = s.querySelector(".device-section__title")?.textContent?.trim() ?? "";
      const cards = [...s.querySelectorAll(".device-card")].map((c) => c.getAttribute("data-device-id"));
      return { title, cards };
    });
  });
  check("2 sections rendered (wifi + usb)", sectionGroups.length === 2, JSON.stringify(sectionGroups));
  check("WiFi section has 2 relay cards",
    sectionGroups[0]?.cards?.length === 2 && sectionGroups[0].cards.every((id) => id.startsWith("node:")),
    JSON.stringify(sectionGroups[0]));
  // USB section has 2 serial + 1 uart = 3 cards
  check("USB section has 3 cards (2 serial + 1 uart)",
    sectionGroups[1]?.cards?.length === 3, JSON.stringify(sectionGroups[1]?.cards));

  // ----- Close dialog via ✕ button -----------------------------------------
  const beforeCloseOpen = await page.evaluate(() =>
    document.querySelector("dialog.device-list")?.hasAttribute("open") ?? false);
  check("dialog is open before ✕ click", beforeCloseOpen === true);

  await page.evaluate(() => {
    const btn = document.querySelector("dialog.device-list .device-list__close");
    btn?.click();
  });
  await sleep(200);

  const afterCloseOpen = await page.evaluate(() =>
    document.querySelector("dialog.device-list")?.hasAttribute("open") ?? false);
  check("dialog closed after ✕ click", afterCloseOpen === false);
  await page.screenshot({ path: path.join(outDir, "dl-02-dialog-closed.png"), fullPage: true });

  // ----- No console errors ------------------------------------------------
  check("no console errors on DeviceList interaction", consoleErrors.length === 0,
    consoleErrors.slice(0, 5).join(" | "));

  // ----- Empty-state fallback (reload w/o mock) ---------------------------
  // We can't easily disable the mock mid-run, but we can verify the empty
  // message exists as a CSS element by inspecting DeviceList source —
  // the static test above covers that.

} finally {
  await browser.close();
}

// ----- Summary ------------------------------------------------------------
fs.writeFileSync(
  path.join(outDir, "device-list-e2e-results.json"),
  JSON.stringify({ url, results }, null, 2),
);
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (failed > 0) {
  console.log("Failed:");
  for (const r of results.filter((r) => !r.ok))
    console.log(`  ✗ ${r.name}  ${r.detail ?? ""}`);
}
process.exit(failed ? 1 : 0);
