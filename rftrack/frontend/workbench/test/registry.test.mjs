/**
 * 组件注册表单测。这里钉的是"**未声明即不支持**"这条纪律的数据侧：
 * 今天点不了的条目必须自带理由与阶段，页面才能原样把那句话显示出来，而不是灰掉一个按钮让人猜。
 *
 * 后面那三条"归哪一边 / 指针不烂"的判据是 P0 阶段 3.4 加的，防的都是同一类假话：
 * 声明里的文字说得越具体，就越容易在别的文件动完之后变成谎话——所以这里不去读措辞，
 * 而是把措辞里引用的**符号、文件、工作区前缀**拿回真实出处对一遍。
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import * as registry from "../registry.js";
import { WORKSPACES } from "../workspaces.js";

const ROOT = new URL("../../../..", import.meta.url);
/** 理由里的路径有两种基准点：声明者自己住的那一层，和仓库根。 */
const WORKBENCH = new URL("..", import.meta.url);
/** 理由里引用的后端符号：按文件名去仓库里找。 */
const BACKEND_CAPTURE = "rftrack/backend/app/services/capture.py";

/**
 * 带路径的 token 必须真的在某一处存在。允许两个基准点不是和稀泥：`vendor/waterfall-lut.json`
 * 按写它的人所在的工作台目录指，`web/src/util/canvas.ts` 按仓库根指——两种写法都在读同一份
 * 事实，判据只管"指不到地方的话不许写在页面上"。
 */
function pointerResolves(file) {
  return existsSync(new URL(file, WORKBENCH)) || existsSync(new URL(file, ROOT));
}

test("十三个组件按来源分两路：实时推送与 REST 拉取，没有第三路", () => {
  assert.deepEqual(registry.COMPONENTS.map((entry) => entry.id), [
    "channel-matrix", "channel-detail", "combined-spectrum", "rssi-trend", "signal-events", "node-status", "site-tree",
    "channel-activity-heatmap", "target-inspector", "alert-feed",
    "node-controls", "node-console", "node-rawiq",
  ]);
  assert.deepEqual(
    registry.COMPONENTS.filter((entry) => entry.source === "live").map((entry) => entry.id),
    ["channel-matrix", "channel-detail", "combined-spectrum", "signal-events"]
  );
  assert.deepEqual(
    registry.COMPONENTS.filter((entry) => entry.source === "api").map((entry) => entry.id),
    ["rssi-trend", "node-status", "site-tree", "channel-activity-heatmap", "target-inspector", "alert-feed",
      "node-controls", "node-console", "node-rawiq"]
  );
  // S5 那一路 `demo` 必须彻底不在：留着它，`registry.sourceWord("demo")` 就会给一格
  // 读不到真数的组件挂上"演示"这个词，而这一页再没有演示数据可演示。
  assert.equal(registry.COMPONENTS.some((entry) => entry.source === "demo"), false);
});

test("每条声明都带尺寸、下限、需要哪些能力；source 只有 live/api 两种", () => {
  for (const entry of registry.COMPONENTS) {
    assert.ok(entry.size.w >= entry.min.w && entry.size.h >= entry.min.h, `${entry.id} 默认尺寸不许小于自身下限`);
    assert.ok(entry.min.w >= 2 && entry.min.h >= 1, `${entry.id} 的下限不许破栅格的 MIN_W`);
    assert.ok(entry.size.w <= 12, `${entry.id} 不许比整幅画布还宽`);
    assert.ok(Array.isArray(entry.needs) && entry.needs.length, `${entry.id} 必须声明依赖哪一路能力`);
    assert.ok(["live", "api"].includes(entry.source), `${entry.id} 的来源必须可判定`);
    assert.ok(typeof entry.summary === "string" && entry.summary.length > 0);
    // `needs` 只能写 `context.capabilities()` 给得出的那三个词，写了别的永远不会满足，
    // 这一条就会在库里永远是灰的——灰到底不是"还没到"，是拼错了。
    for (const need of entry.needs) {
      assert.ok(["live", "nodes", "hierarchy"].includes(need), `${entry.id} 依赖了不存在的能力 ${need}`);
    }
  }
});

test("来源词只认 live/api：认不出的来源要说「未知来源」而不是借一个已知的词", () => {
  assert.equal(registry.sourceWord("live"), "实时推送");
  assert.equal(registry.sourceWord("api"), "REST 拉取");
  assert.equal(registry.sourceWord("demo"), "未知来源 demo");
});

test("每一格都要看能力的脸色：一条能力都没有时全部点不了", () => {
  const bare = registry.library({ capabilities: [] });
  const live = bare.filter((entry) => entry.kind === "live");
  assert.equal(live.every((entry) => entry.available === false), true, "没有通路就声称可点是假话");
  assert.match(live.find((entry) => entry.id === "channel-matrix").reason, /缺少能力：live/);
  assert.match(live.find((entry) => entry.id === "node-status").reason, /缺少能力：nodes/);

  const full = registry.library({ capabilities: ["live", "nodes", "hierarchy"] });
  assert.equal(full.every((entry) => entry.kind === "planned" || entry.available), true);

  // 只订上推送、层级还没加载：矩阵该可点，树该灰着。
  const half = registry.library({ capabilities: ["live"] });
  assert.equal(half.find((entry) => entry.id === "channel-matrix").available, true);
  assert.equal(half.find((entry) => entry.id === "site-tree").available, false);
});

test("未上线条目必须写清理由与阶段，空着不写才是问题", () => {
  // S8 落地时 `channel-activity-heatmap` 与 `target-inspector` 已从 PLANNED 升入 COMPONENTS：
  // 升级的方式是"搬走"，不是"PLANNED 里再抄一份"——两头都有，页面上就会出现两个同名条目。
  assert.ok(registry.PLANNED.length >= 3);
  const plannedIds = registry.PLANNED.map((entry) => entry.id);
  for (const promoted of ["channel-activity-heatmap", "target-inspector"]) {
    assert.equal(plannedIds.includes(promoted), false, `${promoted} 已上线，不该还留在未上线清单里`);
    assert.ok(registry.KNOWN_IDS.has(promoted), `${promoted} 升级后必须出现在 COMPONENTS`);
  }
  for (const entry of registry.PLANNED) {
    assert.equal(entry.available, false);
    assert.ok(entry.reason.length > 0, `${entry.id} 缺理由`);
    if (entry.stage === null) {
      // 没有阶段的那一条就是"这事我们不做"，理由必须落到规范上，否则后人会当成漏掉的排期。
      assert.match(entry.reason, /规范|不做|没有/, `${entry.id} 说不上为什么永远不上`);
    } else {
      // 两种排期写法：S 系列那份历史里程碑，和 P0 计划那份阶段号。写了别的阶段号，
      // 下一条用例（拿计划文档对一遍）会红。
      assert.match(entry.stage, /^(S\d+|P0 阶段 \d+)$/, `${entry.id} 的阶段写法没人认得：页面会显示「计划 没人说的话」`);
    }
  }
  const spectrum = registry.PLANNED.find((entry) => entry.id === "spectrum-view");
  assert.match(spectrum.reason, /连续频谱/, "把离散频道当频谱是 §2.3 禁止的那件事，必须在数据里就说明白");
});

test("每条声明都归一个工作区：site 与 single-node 两种话不能混", () => {
  const ids = WORKSPACES.map((entry) => entry.id);
  for (const entry of registry.COMPONENTS.concat(registry.PLANNED)) {
    assert.ok(ids.includes(entry.workspace), `${entry.id} 的 workspace=${entry.workspace} 不在工作区清单里`);
  }
  // 这一页能加的格子全是站点侧的：单机侧的格子由 `/app/` 那一页自己声明，不进这份注册表。
  assert.deepEqual([...new Set(registry.COMPONENTS.map((entry) => entry.workspace))], ["site"]);
  const planned = new Set(registry.PLANNED.map((entry) => entry.workspace));
  assert.ok(planned.has("site") && planned.has("single-node"), "未上线清单里两种都没有，标注就只是装饰");

  // 归另一边的那几条必须把人送到哪一边说出口，而且用的就是 workspaces.js 里那个前缀：
  // 在数据里抄一遍 "/app/" 的话，改挂载点时这里会留下一个指不到地方的地址。
  const base = WORKSPACES.find((entry) => entry.id === "single-node").base;
  for (const entry of registry.PLANNED.filter((row) => row.workspace === "single-node")) {
    assert.ok(entry.reason.includes(base), `${entry.id} 说归单机控制台却没提 ${base}`);
  }
});

test("声明里的指针要还能对上真实出处：行号指针一律算过期", () => {
  for (const entry of registry.PLANNED) {
    // 这次的起因：reason 里写着 `capture.py:573`，实际早已移到 649——那种数字每提交一次
    // 就腐烂一次，而红不会来。所以这里反过来断言：理由里不许出现「文件:行号」。
    const linePointers = entry.reason.match(/[\w/.-]+\.(py|ts|js|mjs|css|html|json):\d+/g) || [];
    assert.deepEqual(linePointers, [], `${entry.id} 用行号指针指路（${linePointers.join(" ")}），改一次提交就烂一次`);

    // 只查带路径的写法：`capture.py` 那样光写个文件名是在说符号归谁，不是指针。
    // 后缀必须整词匹配且列全：`\.js` 抢在 `json` 前头会把 `vendor/waterfall-lut.json` 削成
    // 一个不存在的 `.js`；漏了 `ts` 则是让 `web/src/util/canvas.ts` 整条不被检查——
    // 两种都是判据自己悄悄少看一眼。
    const PATH_POINTER = /[\w.-]+\/[\w./-]+\.(?:tsx|ts|py|mjs|js|json|css|html)(?![\w-])/g;
    for (const file of entry.reason.match(PATH_POINTER) || []) {
      assert.ok(pointerResolves(file), `${entry.id} 引用的文件不存在：${file}`);
    }
    if (entry.reason.includes("capture.py")) {
      const source = readFileSync(new URL(BACKEND_CAPTURE, ROOT), "utf8");
      assert.match(source, /def _frame/, `${entry.id} 说的 capture.py 丢弃分支已经改名（_frame）`);
      assert.match(source, /FRAME_NAMES/, `${entry.id} 说的丢弃名单已经改名（FRAME_NAMES）`);
      assert.ok(!source.includes("services/capture.py:573"), "指针不该再被抄回理由里");
    }
  }
});

test("写了阶段的条目，那个阶段在 P0 计划里确实存在", () => {
  const plan = readFileSync(new URL("docs/superpowers/plans/2026-11-03-widscan58-p0-stage1-audit.md", ROOT), "utf8");
  const staged = registry.PLANNED.filter((entry) => /^P0 阶段 \d+$/.test(entry.stage || ""));
  assert.ok(staged.length >= 2, "频谱与瀑布图按 #6/#7 已排进 P0，不该还写成没有阶段");
  for (const entry of staged) {
    const number = /(\d+)$/.exec(entry.stage)[1];
    assert.ok(plan.includes(`### 阶段 ${number} —`), `${entry.id} 说排进 ${entry.stage}，计划里没有这一节`);
  }
});

test("component() 认不到就回 null，而不是抛错——坏布局要能降级渲染", () => {
  assert.equal(registry.component("nope"), null);
  assert.ok(registry.component("channel-matrix"));
  assert.equal(registry.KNOWN_IDS.has("site-tree"), true);
  assert.equal(registry.KNOWN_IDS.has("spectrum-view"), false, "未上线的条目不算已注册的组件");
});

test("库里两半拼起来：live 在前、planned 在后，planned 也给尺寸以免页面为它写特例", () => {
  const rows = registry.library({ capabilities: ["demo", "nodes", "hierarchy"] });
  assert.equal(rows.length, registry.COMPONENTS.length + registry.PLANNED.length);
  const firstPlanned = rows.findIndex((entry) => entry.kind === "planned");
  assert.equal(firstPlanned, registry.COMPONENTS.length);
  for (const entry of rows.filter((row) => row.kind === "planned")) {
    assert.ok(entry.size && entry.size.w > 0 && entry.size.h > 0);
  }
});
