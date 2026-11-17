// Monitor workspace: free-layout dragging, docking, resizing, collapsing,
// persistence and the stacked fallback. GPL-3.0-or-later.
//
//   node e2e/workspace.mjs [--url http://localhost:5173/] [--chrome /path/to/chrome]
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import {
  parseCliArgs,
  resolveChromeExecutable,
  formatChromeNotFound,
} from "./chrome-resolver.mjs";

const { values } = parseCliArgs(process.argv.slice(2), {
  extraKnown: { "--url": "string" },
});
const base = values["--url"] ?? "http://localhost:5173/";
const resolved = resolveChromeExecutable({ cliPath: values["--chrome"] });
if (resolved.source === "none") {
  console.error(formatChromeNotFound(resolved));
  process.exit(1);
}
fs.mkdirSync("e2e-out", { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};
const shot = (name) => page.screenshot({ path: `e2e-out/${name}.png`, fullPage: true });

const browser = await puppeteer.launch({ executablePath: resolved.path, headless: true, args: ["--no-sandbox"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 2600, deviceScaleFactor: 1 }); // tall: every panel reachable
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
await page.goto(`${base}?sim`, { waitUntil: "networkidle0" });
await page.evaluate(() => localStorage.removeItem("widscan58.layout.monitor"));
await page.reload({ waitUntil: "networkidle0" });
await sleep(2500);

const panelState = () =>
  page.evaluate(() => {
    const main = document.querySelector("main.workspace");
    const panels = [...document.querySelectorAll(".panel")];
    const rect = (id) => {
      const el = document.querySelector(`.panel[data-panel="${id}"]`);
      const r = el.getBoundingClientRect();
      return { left: r.left, top: r.top, width: r.width, height: r.height };
    };
    const saved = JSON.parse(localStorage.getItem("widscan58.layout.monitor") ?? "null");
    return {
      cls: main.className,
      count: panels.length,
      position: panels.length ? getComputedStyle(panels[0]).position : "",
      channels: rect("channels"),
      panorama: rect("panorama"),
      zoom: rect("zoom"),
      controls: rect("controls"),
      saved,
      dropHidden: document.querySelector(".panel-drop").hidden,
      mainHeight: main.getBoundingClientRect().height,
    };
  });

const dragTo = async (handle, tx, ty) => {
  const box = await handle.boundingBox();
  const x = box.x + Math.min(40, box.width / 2);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(tx, ty, { steps: 8 });
  await sleep(120);
  const ghostShown = await page.evaluate(() => !document.querySelector(".panel-drop").hidden);
  await page.mouse.up();
  await sleep(250);
  return ghostShown;
};
const ws = await page.evaluate(() => {
  const r = document.querySelector("main.workspace").getBoundingClientRect();
  return { left: r.left, right: r.right };
});

let s = await panelState();
check("wide screen starts in the free layout", /\bfree\b/.test(s.cls), s.cls);
check("all seven panels are wrapped", s.count === 7, `${s.count}`);
check("panels are absolutely placed", s.position === "absolute", s.position);
check("no saved layout before the first change", s.saved === null, JSON.stringify(s.saved));
await shot("workspace-1-default");

// Drag the panorama down by three rows: the ghost previews, then the move commits.
const panoHead = await page.$('.panel[data-panel="panorama"] .panel-head');
const beforePano = s.panorama.top;
const panoBox = await panoHead.boundingBox();
const ghost = await dragTo(panoHead, panoBox.x + 40, panoBox.y + panoBox.height / 2 + 120);
s = await panelState();
check("dragging shows the drop preview", ghost);
check("panorama moves down three rows", Math.round(s.panorama.top - beforePano) === 120, `${Math.round(s.panorama.top - beforePano)}px`);
check("the move is saved", s.saved?.panels?.panorama?.y === 12, JSON.stringify(s.saved?.panels?.panorama));

// Dock controls to the left half, then to the right half.
const ctlHead = await page.$('.panel[data-panel="controls"] .panel-head');
const ctlBox = await ctlHead.boundingBox();
await dragTo(ctlHead, ws.left + 5, ctlBox.y + ctlBox.height / 2);
s = await panelState();
check("left dock snaps to column 0, half width", Math.abs(s.controls.left - 16) <= 1 && Math.abs(s.controls.width - 698) <= 2, `${s.controls.left}/${s.controls.width}`);
await dragTo(ctlHead, ws.right - 5, ctlBox.y + ctlBox.height / 2);
s = await panelState();
check("right dock snaps to column 6", Math.abs(s.controls.left - 726) <= 2 && Math.abs(s.controls.width - 698) <= 2, `${s.controls.left}/${s.controls.width}`);
await shot("workspace-2-docked");

// Resize zoom from its bottom-right grip: two columns wider.
const zoomGrip = await page.$('.panel[data-panel="zoom"] .panel-resize');
const beforeZoom = s.zoom.width;
await zoomGrip.evaluate((el) => el.scrollIntoView({ block: "center" }));
await sleep(150);
const gripBox = await zoomGrip.boundingBox();
await dragTo(zoomGrip, gripBox.x + gripBox.width / 2 + 220, gripBox.y + gripBox.height / 2);
s = await panelState();
check("the resize grip widens the panel", Math.round(s.zoom.width - beforeZoom) === 237, `${Math.round(s.zoom.width - beforeZoom)}px`);
check("the new width is saved", s.saved?.panels?.zoom?.w === 8, JSON.stringify(s.saved?.panels?.zoom));

// Collapse channels: the body hides and the section shrinks.
const beforeH = s.channels.height;
await page.click('.panel[data-panel="channels"] .panel-collapse');
await sleep(400);
s = await panelState();
const collapsed = await page.evaluate(() => ({
  cls: document.querySelector('.panel[data-panel="channels"]').className,
  body: getComputedStyle(document.querySelector('.panel[data-panel="channels"] .panel-body')).display,
  saved: JSON.parse(localStorage.getItem("widscan58.layout.monitor")).panels.channels,
}));
check("collapse hides the panel body", /\bcollapsed\b/.test(collapsed.cls) && collapsed.body === "none", collapsed.cls);
check("collapse shrinks the panel to its header", s.channels.height < beforeH && s.channels.height < 100, `${s.channels.height}px`);
check("collapse is saved", collapsed.saved?.collapsed === true && collapsed.saved?.expandedH > 2, JSON.stringify(collapsed.saved));
await shot("workspace-3-collapsed");

// Reload: everything comes back.
await page.reload({ waitUntil: "networkidle0" });
await sleep(2500);
s = await panelState();
const restored = await page.evaluate(() => document.querySelector('.panel[data-panel="channels"]').className);
check("layout survives a reload", /\bfree\b/.test(s.cls) && s.saved?.panels?.zoom?.w === 8 && /\bcollapsed\b/.test(restored), s.saved?.panels?.zoom && JSON.stringify(s.saved.panels.zoom));
check("the collapsed panel stays collapsed", s.channels.height < 100, `${s.channels.height}px`);

// Expand again, then reset from the menu.
await page.click('.panel[data-panel="channels"] .panel-collapse');
await sleep(400);
const menuPick = async (label) => {
  await page.click(".menu-btn");
  const [item] = await page.$$(`xpath/.//button[contains(@class,'menu-item') and contains(., '${label}')]`);
  await item.click();
  await sleep(400);
};
await menuPick("Reset layout");
s = await panelState();
check("reset restores the default sizes", s.saved?.panels?.zoom?.w === 6 && s.saved?.panels?.panorama?.y === 9, JSON.stringify(s.saved?.panels));
const overlap = await page.evaluate(() => {
  const rs = [...document.querySelectorAll(".panel")].map((e) => e.getBoundingClientRect());
  const bad = [];
  for (let i = 0; i < rs.length; i++)
    for (let j = i + 1; j < rs.length; j++) {
      const a = rs[i], b = rs[j];
      if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) bad.push([i, j]);
    }
  return bad;
});
check("panels do not overlap after reset", overlap.length === 0, JSON.stringify(overlap));
await shot("workspace-4-reset");

// Stacked layout via the menu keeps the old grid flow.
await menuPick("Stacked layout");
s = await panelState();
const stacked = await page.evaluate(() => {
  const el = document.querySelector('main.workspace');
  const zoom = document.querySelector('.panel[data-panel="zoom"]');
  const ch = document.querySelector('.panel[data-panel="channels"]');
  const pano = document.querySelector('.panel[data-panel="panorama"]');
  return {
    cls: el.className,
    inline: zoom.style.width || zoom.style.left || zoom.style.height || "",
    position: getComputedStyle(zoom).position,
    flow: ch.getBoundingClientRect().top < pano.getBoundingClientRect().top,
  };
});
check("stacked layout drops the absolute styles", /\bstack\b/.test(stacked.cls) && stacked.inline === "" && stacked.position !== "absolute", JSON.stringify(stacked));
check("stacked layout keeps the column flow", stacked.flow);
await shot("workspace-5-stacked");

// Narrow screens force the stack and disable the free option.
await page.setViewport({ width: 800, height: 900 });
await sleep(500);
s = await panelState();
await page.click(".menu-btn");
const narrow = await page.evaluate(() => {
  const free = [...document.querySelectorAll(".menu-item")].find((b) => b.textContent === "Free layout");
  const note = [...document.querySelectorAll(".menu-note")].find((n) => n.textContent.includes("900"));
  return { cls: document.querySelector("main.workspace").className, disabled: free?.disabled, note: note?.textContent ?? "" };
});
await page.keyboard.press("Escape");
check("narrow screens force the stacked layout", /\bstack\b/.test(narrow.cls), narrow.cls);
check("free layout is disabled and explained", narrow.disabled === true && narrow.note.length > 0, narrow.note);
await shot("workspace-6-narrow");

await page.setViewport({ width: 1440, height: 2600 });
await sleep(500);
s = await panelState();
check("the user's stack choice survives widening", /\bstack\b/.test(s.cls), s.cls);
await menuPick("Free layout");
s = await panelState();
check("free layout can be chosen again", /\bfree\b/.test(s.cls) && s.position === "absolute", s.cls);
check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));

await page.screenshot({ path: "e2e-out/workspace-free.png", fullPage: true });
await browser.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
