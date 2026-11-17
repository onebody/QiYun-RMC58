/**
 * 瀑布图 256 项色表的对拍（P0 阶段 3.3，工作台这一侧）。
 *
 * 三条判据都在防一种具体的假话：
 * - **逐字节对拍基线**：`buildLut()` 是本仓库无构建 ESM 那一侧的实现，基线是提交进仓库的
 *   `vendor/waterfall-lut.json`。两侧不通气，谁改了插值就红——这正是"两套构建体系不能
 *   互相 import"唯一不重复开发的路（TS 侧的对拍在 `web/src/util/canvas.lut.test.ts`）。
 * - **色标锚在 `web/src/styles.css`**：基线里的 `stops` 必须仍是 CSS 里那七个变量。
 *   只比表不比色标的话，改深一个色标 + 重新生成基线会一路绿灯，而页面上的瀑布图已经换色。
 *   跨目录读 `web/` 与 `tests/test_frame_parity.py` 三份镜像对拍是同一条纪律。
 * - **端点与亮度方向**：表的第 0 项与第 255 项必须就是首末色标本身，亮度按色标自身的
 *   方向单调。这两条不依赖实现，是"这份表确实是从这七个色标来的"的正面证据。
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildLut, checkBaseline, fetchLut, hexToRgb, lutFor } from "../waterfall-lut.js";

const BASELINE_URL = new URL("../vendor/waterfall-lut.json", import.meta.url);
const STYLES_URL = new URL("../../../../web/src/styles.css", import.meta.url);
const baseline = JSON.parse(readFileSync(BASELINE_URL, "utf8"));
const stylesCss = readFileSync(STYLES_URL, "utf8");

const THEME_NAMES = Object.keys(baseline.themes).sort();

/** CSS 里某个选择器块内的 `--seq-0..6`，按声明顺序取。 */
function stopsFromCss(selector) {
  const at = stylesCss.indexOf(selector + " {");
  assert.notEqual(at, -1, `styles.css 里找不到选择器 ${selector}`);
  const block = stylesCss.slice(at, stylesCss.indexOf("\n}", at));
  return [0, 1, 2, 3, 4, 5, 6].map((j) => {
    const found = new RegExp(`--seq-${j}:\\s*(#[0-9a-fA-F]{6})`).exec(block);
    assert.ok(found, `${selector} 块里缺 --seq-${j}`);
    return found[1].toLowerCase();
  });
}

const brightness = (rgb) => rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114;

test("基线形状：两套主题、256 项 × 3 通道、全部是 0–255 的整数", () => {
  assert.deepEqual(checkBaseline(baseline), []);
  assert.deepEqual(THEME_NAMES, ["dark", "light"], "瀑布图两套主题都要有：切了主题没有表就是空白瀑布");
  assert.equal(baseline.entries, 256);
  assert.equal(baseline.channels, 3);
});

for (const name of THEME_NAMES) {
  const theme = baseline.themes[name];

  test(`工作台侧 buildLut 与基线逐字节相等：${name} 256/256 项`, () => {
    const mine = buildLut(theme.stops);
    const mismatches = [];
    for (let j = 0; j < 256; j++) {
      const theirs = theme.lut.slice(j * 3, j * 3 + 3);
      const mineSlice = [mine[j * 3], mine[j * 3 + 1], mine[j * 3 + 2]];
      if (theirs.some((v, c) => v !== mineSlice[c])) mismatches.push(`${j}: 基线 ${theirs} vs 本侧 ${mineSlice}`);
    }
    assert.deepEqual(mismatches, [], `${mismatches.length} 项插值不一致（前 5 条：${mismatches.slice(0, 5).join(" | ")}）`);
  });

  test(`基线色标仍是 styles.css 里的那七个变量：${name}`, () => {
    assert.deepEqual(stopsFromCss(theme.selector), theme.stops.map((h) => h.toLowerCase()),
      `改了 ${theme.selector} 的 --seq-* 就得重跑 rftrack/tools/make_waterfall_lut.mjs`);
  });

  test(`表的端点就是首末色标本身：${name}`, () => {
    const lut = buildLut(theme.stops);
    assert.deepEqual([lut[0], lut[1], lut[2]], hexToRgb(theme.stops[0]), "第 0 项必须是 --seq-0");
    assert.deepEqual([lut[765], lut[766], lut[767]], hexToRgb(theme.stops[6]), "第 255 项必须是 --seq-6");
  });

  test(`亮度沿色标自身方向单调，不许中段打结：${name}`, () => {
    const stops = theme.stops.map(hexToRgb).map(brightness);
    const rising = stops[6] > stops[0];
    const lut = buildLut(theme.stops);
    let previous = null;
    const breaks = [];
    for (let j = 0; j < 256; j++) {
      const value = brightness([lut[j * 3], lut[j * 3 + 1], lut[j * 3 + 2]]);
      if (previous !== null && (rising ? value < previous - 0.5 : value > previous + 0.5)) {
        breaks.push(`${j}`);
      }
      previous = value;
    }
    assert.deepEqual(breaks, [], `第 ${breaks.slice(0, 8).join(",")} 项亮度逆着色标方向跳了`);
  });
}

test("lutFor 只认基线里有的主题，认不到就说清而不是回一张空表", () => {
  assert.equal(lutFor(baseline, "dark").length, 768);
  assert.ok(lutFor(baseline, "dark") instanceof Uint8ClampedArray, "canvas 的 ImageData 要的就是这个类型");
  assert.throws(() => lutFor(baseline, "sepia"), /没有主题/);
});

test("fetchLut：非 200 与形状不对都抛错，不静默回一张表", async () => {
  const ok = await fetchLut({
    theme: "dark",
    fetchImpl: async () => ({ ok: true, json: async () => baseline }),
  });
  assert.deepEqual(Array.from(ok.lut.slice(0, 3)), baseline.themes.dark.lut.slice(0, 3));
  assert.equal(ok.doc, baseline);

  await assert.rejects(() => fetchLut({ fetchImpl: async () => ({ ok: false, status: 404 }) }), /404/);
  await assert.rejects(
    () => fetchLut({ fetchImpl: async () => ({ ok: true, json: async () => ({ entries: 128, channels: 3, themes: {} }) }) }),
    /形状不对/
  );
});

test("fetchLut：没有 fetch 可注入也没全局 fetch 时，说的是这件事而不是「doFetch 不是函数」", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = undefined;
  try {
    await assert.rejects(() => fetchLut({}), /没有 fetch/);
  } finally {
    globalThis.fetch = original;
  }
});
