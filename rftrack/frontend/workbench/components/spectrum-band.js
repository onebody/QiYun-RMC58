/**
 * 频谱带（`spectrum-band`）：**阶段 4 的接入点，今天只有外壳**。
 *
 * 为什么文件现在就进来：P0 计划把 `components/spectrum-band.js` 列在阶段 3 的产出里，
 * 目的是让阶段 4 的 `/api/spectrum` / `/ws/spectrum` 到位时有一个明确的落点，而不是
 * 到时候再决定"这一格归谁、缺什么"。
 *
 * 为什么它**不进** `components/index.js` 的实现表：那张表与 `registry.COMPONENTS` 一一对
 * （`test/components.test.mjs` 拿注册表对账）。把一条还没有数据的通路声明成"可点的格子"，
 * 页面上就会多一块能加进布局、加进去却永远空着的组件——那正是规范 §2.3 禁止的那件事，
 * 也是 R28 登记过的缺陷类别。所以今天它只以 `registry.PLANNED` 里 `spectrum-view`
 * 那一条的身份出现在组件库面板里。
 *
 * 这个文件里只放两样今天就有真值的东西：
 * 1. `MISSING`：频谱带画不出来缺哪三条通路，逐条对着**后端与页面的现状**写，不对着设想写。
 *    `test/spectrum-band.test.mjs` 会拿源码验这三条今天仍然成立——阶段 4 落地时它们会翻红，
 *    那就是"可以升入 COMPONENTS 了"的信号，而不是让这里自己说"应该已经好了"。
 * 2. `create()`：把这些话说出来的工厂。阶段 4 换掉的是取数那一行，判据与措辞留在原地。
 */

import { empty } from "./parts.js";

export const plugin_id = "spectrum-band";

/** 与 `registry.PLANNED` 里 `spectrum-view` 同一条排期：这里不另写一个阶段号。 */
export const STAGE_POINTER = "spectrum-view";

/**
 * 今天画不出频谱带的三条缺口。`check` 是给测试用的**判据名**，不是给人看的话：
 * 每条都必须在仓库里有一个可以 grep 的事实，说"没通"就得能当场验。
 */
export const MISSING = [
  {
    key: "capture",
    text: "采集层仍把频谱帧计入 skipped_frames 后整帧丢弃（capture.py 的 `_frame()` 只留带 channels 的帧，名单在 `FRAME_NAMES`）",
    check: "discard",
    lands: "阶段 4 的丢弃分支拆分：SPECTRUM 走栅格，RAWIQ/TELEMETRY/LOG 仍只计数",
  },
  {
    key: "grid",
    text: "没有频谱栅格：`spectrum_grid.py` 与 `spectrum_capture` 那张表都还不存在，逐 bin 取最大没有落脚的地方",
    check: "no_grid",
    lands: "`010_spectrum_grid` + `spectrum_grid.py`（350 bin、同 detector 逐 bin MAX、缺帧记账）",
  },
  {
    key: "path",
    text: "页面这一侧没有取数口：`/api/spectrum` 与 `/ws/spectrum` 都还没开，`/ws/live` 只发 samples 与 event 两类",
    check: "no_endpoint",
    lands: "计划 §4 第 12/13 条：`GET /api/spectrum?node=&mode=`（detector 之外显式 422）与 `WS /ws/spectrum`（沿用丢最旧口径）",
  },
];

/** 缺口清单 → 人话：一条不落全说出来，因为"只说第一条"会让人以为补一条就能画。 */
export function notice(missing = MISSING) {
  return missing.map((entry) => `${entry.text}（要等：${entry.lands}）`);
}

export function create(rt, deps) {
  const { h } = rt;
  const planned = deps.registry.PLANNED.find((entry) => entry.id === STAGE_POINTER);

  return {
    name: "SpectrumBand",
    props: { widget: { type: Object, required: true } },
    setup(props) {
      return () => h("div", { class: "spectrum-band" }, [
        // 空状态说的是"这条通路还没通"，不是"这块板今天没读数"——后者会让人去查天线。
        // 这里不写阶段号：等哪个阶段只由 `registry.PLANNED` 那一条说，hint 位就是它。
        empty(rt, "频谱带还画不出来：频谱取数那一路还没接入，取数通路还没通。",
          planned ? planned.stage : "注册表里查不到这一条的出处"),
        h("ul", { class: "spectrum-band-missing" }, notice().map((line) => h("li", line))),
        planned
          ? h("p", { class: "muted" }, `组件库里的这一条叫什么、归哪一边、等哪个阶段，都由注册表说：${planned.name} · ${deps.registry.workspaceWord(planned.workspace)}`)
          : h("p", { class: "warn" }, "注册表里没有 spectrum-view：这条格子的排期失去出处，请把它补回去或删掉这个组件。"),
      ]);
    },
  };
}
