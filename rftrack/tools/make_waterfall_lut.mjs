// 瀑布图 256 项色表基线的生成器（P0 阶段 3.3）。
//
// 为什么要有这份文件：`web/src/util/canvas.ts`（Vite + TypeScript）与工作台
// `rftrack/frontend/workbench/`（无构建 ESM，用户 2026-10-28 拍板的形态）互相 import 不了，
// 于是同一张色表在两边各有一份实现。把数值复制过去就是两处真值——改了一边，另一边照样绿。
// 这里的做法是**只生成、不手写**：直接跑真 TS 源的 `seqLut()` 算出数值，落到一份提交进仓库的
// JSON 基线；两边再各自独立插值与它对拍（`web/src/util/canvas.lut.test.ts` 与
// `rftrack/frontend/workbench/test/waterfall-lut.test.mjs`）。
//
// 色标的真值在 `web/src/styles.css` 的 `--seq-0..6`（暗/亮各一组），本脚本从那里读，
// 不在这里另写一遍。对拍测试还会单独校验基线里的 stops 仍是 CSS 里那七个，
// 所以"改了色标 + 重跑本脚本"不会一路绿灯——那是页面已经换色、瀑布图基线却跟着改的假话。
//
// 用法：node --experimental-strip-types rftrack/tools/make_waterfall_lut.mjs
// 重跑应当只改 `generated` 以外什么都没有：先 `git diff` 看一眼，再决定要不要提交。

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { invalidateTheme, seqLut } from "../../web/src/util/canvas.ts";

const here = dirname(fileURLToPath(import.meta.url));
const STYLES = resolve(here, "../../web/src/styles.css");
const OUT = resolve(here, "../frontend/workbench/vendor/waterfall-lut.json");

const ENTRIES = 256;
const CHANNELS = 3;

/** CSS 里某个选择器块内的 `--seq-0..6`（按声明顺序），一个都不许缺。 */
function stopsFromCss(css, selector) {
  const at = css.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`styles.css 里找不到选择器 ${selector}`);
  const end = css.indexOf("\n}", at);
  const block = css.slice(at, end === -1 ? css.length : end);
  return [0, 1, 2, 3, 4, 5, 6].map((j) => {
    const found = new RegExp(`--seq-${j}:\\s*(#[0-9a-fA-F]{6})`).exec(block);
    if (!found) throw new Error(`${selector} 块里缺 --seq-${j}`);
    return found[1].toLowerCase();
  });
}

/**
 * 让 `canvas.ts` 以为自己在浏览器里，并把 `getComputedStyle` 换成"读这一组色标"。
 *
 * `seqLut()` 走的是 `theme()`，而 `theme()` 会把整套变量都读一遍——只有 `--seq-*` 进得了
 * 这张表，其余的读回来是空串、没人用。所以这里只认 `--seq-0..6`，别的变量给空串而不是报错：
 * `theme()` 以后再加变量不该把生成器弄坏，加的是 `--seq-7` 那种则会因为 CSS 里取不到而红。
 *
 * 换主题靠清掉模块级缓存再重设假实现，与页面上切 `data-theme` 后 `invalidateTheme()` 的走法一致。
 */
function withStops(stops, run) {
  const previous = { document: globalThis.document, getComputedStyle: globalThis.getComputedStyle };
  globalThis.document = { documentElement: {} };
  globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => {
      const found = /^--seq-([0-6])$/.exec(name);
      return found ? stops[Number(found[1])] : "";
    },
  });
  invalidateTheme();
  try {
    return run();
  } finally {
    invalidateTheme();
    if (previous.document === undefined) delete globalThis.document;
    else globalThis.document = previous.document;
    if (previous.getComputedStyle === undefined) delete globalThis.getComputedStyle;
    else globalThis.getComputedStyle = previous.getComputedStyle;
  }
}

const css = readFileSync(STYLES, "utf8");

const themes = {};
for (const selector of [":root", ':root[data-theme="light"]']) {
  const stops = stopsFromCss(css, selector);
  const name = selector === ":root" ? "dark" : "light";
  const lut = withStops(stops, () => seqLut());
  if (lut.length !== ENTRIES * CHANNELS) {
    throw new Error(`${name}: seqLut() 返回 ${lut.length} 项，应为 ${ENTRIES * CHANNELS}`);
  }
  themes[name] = { selector, stops, lut: Array.from(lut) };
}

const doc = {
  _comment:
    "瀑布图 256 项色表的对拍基线（P0 阶段 3.3）。两套构建体系不能互相 import：数值由" +
    " web/src/util/canvas.ts 的 seqLut() 真算出来（Node --experimental-strip-types 直接跑 TS 源），" +
    "色标取自 web/src/styles.css 的 --seq-0..6。两侧各自独立插值再与这份基线逐字节对拍：" +
    "web 侧 tests=web/src/util/canvas.lut.test.ts，工作台侧 test=rftrack/frontend/workbench/test/waterfall-lut.test.mjs。",
  version: 1,
  entries: ENTRIES,
  channels: CHANNELS,
  themes,
};

const text = `${JSON.stringify(doc, null, 1)}\n`;
const previous = (() => {
  try {
    return readFileSync(OUT, "utf8");
  } catch {
    return null;
  }
})();
writeFileSync(OUT, text);

const summary = Object.entries(themes)
  .map(([name, t]) => `${name} ${t.stops[0]}→${t.stops[6]}（${t.lut.length} 项）`)
  .join("，");
console.log(`${OUT.replace(`${process.cwd()}/`, "")}: ${Buffer.byteLength(text)} 字节，${summary}`);
console.log(
  previous === null
    ? "基线此前不存在，已新建。"
    : previous === text
      ? "与仓库里的内容逐字节相同：色标和插值都没动。"
      : "基线发生变化，检查是不是真的改了 --seq-* 或 seqLut() 的插值。"
);
