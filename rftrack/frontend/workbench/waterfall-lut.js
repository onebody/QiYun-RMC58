/**
 * 瀑布图色表（256 项 RGB）在工作台这一侧的实现与读取。
 *
 * 为什么要有这一份而不直接从 `web/src/util/canvas.ts` import：两套构建体系碰不到彼此
 * ——那边是 Vite + TypeScript，这边是无构建的 ESM（用户 2026-10-28 拍板的前端形态，
 * 局域网部署不许依赖外网也不许有打包步骤）。复制色标会变成两处真值，所以这里把
 * **数值**钉在一份提交进仓库的基线里（`vendor/waterfall-lut.json`），两侧各自独立插值
 * 再与它逐字节对拍：
 * - TS 侧：`web/src/util/canvas.lut.test.ts`（跑真实的 `seqLut()`）
 * - 这一侧：`test/waterfall-lut.test.mjs`（跑下面的 `buildLut()`）
 *
 * 基线由 `rftrack/tools/make_waterfall_lut.mjs` 重新生成，色标的真值在
 * `web/src/styles.css` 的 `--seq-0..6`（两套主题各一组）。
 */

/** `#rrggbb` → [r, g, b]。与 TS 侧同名函数行为一致：不认的写法不猜，直接 NaN。 */
export function hexToRgb(hex) {
  const h = String(hex).replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/**
 * 从色标线性插值出 256 项表。
 *
 * 逐段与 `canvas.ts` 的 `seqLut()` 同式：`x = j/255 × (段数)`，取整段号 `k`，段内比例 `f`，
 * 每通道 `stops[k] + (stops[k+1] - stops[k]) × f`。写回 `Uint8ClampedArray` 时按 JS 的
 * ToUint8Clamp 舍入（0.5 进到 1）——这一条不是风格问题：换成分位截断会让整表偏暗一个色阶，
 * 而瀑布图的亮度台阶正是运营者读电平高低的那件事。
 */
export function buildLut(stops) {
  const rgb = stops.map(hexToRgb);
  const lut = new Uint8ClampedArray(256 * 3);
  for (let j = 0; j < 256; j++) {
    const x = (j / 255) * (rgb.length - 1);
    const k = Math.min(rgb.length - 2, Math.floor(x));
    const f = x - k;
    for (let c = 0; c < 3; c++) lut[j * 3 + c] = rgb[k][c] + (rgb[k + 1][c] - rgb[k][c]) * f;
  }
  return lut;
}

/** 校验一份基线的形状：只查结构，不替页面决定该用哪套主题。 */
export function checkBaseline(doc) {
  const problems = [];
  if (!doc || typeof doc !== "object") return ["基线不是一个对象"];
  if (doc.entries !== 256) problems.push(`entries=${doc.entries}，应为 256`);
  if (doc.channels !== 3) problems.push(`channels=${doc.channels}，应为 3`);
  const themes = doc.themes || {};
  if (!Object.keys(themes).length) problems.push("没有主题那一节");
  for (const [name, theme] of Object.entries(themes)) {
    if (!Array.isArray(theme.stops) || theme.stops.length < 2) problems.push(`${name}: 色标少于两项`);
    if (!Array.isArray(theme.lut) || theme.lut.length !== 256 * 3) problems.push(`${name}: 表长 ${theme.lut && theme.lut.length}，应为 768`);
    if (Array.isArray(theme.lut) && theme.lut.some((v) => !Number.isInteger(v) || v < 0 || v > 255)) {
      problems.push(`${name}: 有非 0–255 整数的通道值`);
    }
  }
  return problems;
}

/** 基线里那套主题的表（`Uint8ClampedArray`，canvas 的 `ImageData` 直接可写）。 */
export function lutFor(doc, theme) {
  const entry = (doc.themes || {})[theme];
  if (!entry) throw new Error(`瀑布图基线里没有主题「${theme}」`);
  return Uint8ClampedArray.from(entry.lut);
}

/**
 * 运行时取表（阶段 4 的频谱带/瀑布图组件用）。
 *
 * 每次挂载都重新 `fetch`：这份 JSON 与页面同源、随同一份发布物部署，service worker 之后
 * 会把它缓存住；为它再加一层模块级缓存，换来的只是"改了基线必须刷新页面之外再重启一次"。
 */
export async function fetchLut({ theme = "dark", url = "./vendor/waterfall-lut.json", fetchImpl } = {}) {
  const doFetch = fetchImpl || globalThis.fetch;
  if (!doFetch) throw new Error("这个环境没有 fetch，瀑布图色表取不回来");
  const response = await doFetch(url);
  if (!response.ok) throw new Error(`瀑布图色表取回失败：${response.status} ${url}`);
  const doc = await response.json();
  const problems = checkBaseline(doc);
  if (problems.length) throw new Error(`瀑布图色表基线形状不对：${problems.join("；")}`);
  return { doc, lut: lutFor(doc, theme) };
}
