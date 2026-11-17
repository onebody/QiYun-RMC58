/**
 * 组件注册表：**元数据，不含实现**。这样它可以在 Node 里被测，也可以在浏览器里被
 * `components/index.js` 拿去和真实组件对账（少一个 / 多一个都会被测试钉住）。
 *
 * 三条纪律直接来自规范：
 * - **能力由声明渲染，未声明即不支持**（§2.3）。库里那些"今天点不了"的条目不是装饰：
 *   它们把"为什么不能用、哪个阶段才可用"写在数据里，页面照原样显示，
 *   而不是灰掉一个按钮让人猜。
 * - `source` 到 S6 只剩两路：`"live"`（`/ws/live` 推来的帧）与 `"api"`（REST 读来的当前事实）。
 *   **S5 那一路 `demo` 已经整份删掉**——每一格读不到真数据就显示空状态，不拿演示数补位。
 *   "这是录的"这件事由数据自己带着（回放帧的 `source` 是 `mock`），角标在 `parts.js`。
 * - `needs` 写的是**这一格要哪一路数据源才说得出话**，由 `context.capabilities()` 那份"今天
 *   真答过话的通路"来判可用：`live` 指"整页那条 `/ws/live` 有场地可订"，`nodes` 指
 *   `GET /api/nodes` 回过，`hierarchy` 指四级层级回过。它判的是**通路**，不是"有没有数据"——
 *   通路在但没有板子，格子照样进 `无数据`，那一句话由 `components/select.js` 说。
 *
 * 第四条是阶段 3.4 加的：每条都带 `workspace`，说这件事归**哪一边**——`site`（这一页）
 * 还是 `single-node`（单机控制台 `/app/`）。P0 差异表里 #16 烧录、#17 功率计、#18 计圈
 * 三项按 C-7 裁定不移植平台侧，它们在这一页不是"坏了"也不是"没排期"，是"在另一边"；
 * 不写这一层，运营者在这一页找烧录入口就只找到一句"不上线"。
 */

import { workspaceById } from "./workspaces.js";

export const COMPONENTS = [
  {
    id: "channel-matrix",
    name: "多频道矩阵",
    summary: "当前这块板每一档的电平、EMA、SNR、活动态与更新时间",
    size: { w: 6, h: 4 },
    min: { w: 3, h: 2 },
    source: "live",
    workspace: "site",
    needs: ["live"],
  },
  {
    id: "channel-detail",
    name: "单频道详情",
    summary: "联动那一档在帧里报出来的全部量：电平、偏移、带宽与饱和",
    size: { w: 3, h: 4 },
    min: { w: 2, h: 3 },
    source: "live",
    workspace: "site",
    needs: ["live"],
    links: "channel",
  },
  {
    id: "combined-spectrum",
    name: "综合电平分线",
    summary: "多节点拉到同一张图上分线对比：一条线一个节点，横轴是档位号——离散频道电平，不是连续频谱",
    size: { w: 6, h: 4 },
    min: { w: 4, h: 3 },
    source: "live",
    workspace: "site",
    // 数据就是整页那条 `/ws/live`（推送里带全场地的帧），勾选哪些节点由 `nodes` 生效值定，
    // 没勾回落到当前单板；`slots` 生效值当横轴筛选。名字里没有"频谱"两个字是刻意的：
    // 真频谱要频率分辨数据，`spectrum-view` 的 PLANNED 条目还在那里等着。
    needs: ["live"],
    links: "channel",
  },
  {
    id: "rssi-trend",
    name: "RSSI 趋势",
    summary: "一段时间内的 RSSI 变化（离散频道序列，不是频谱）",
    size: { w: 6, h: 4 },
    min: { w: 3, h: 3 },
    source: "api",
    workspace: "site",
    // 曲线读的是 `GET /api/samples/summary`（摘要桶），那是 REST 而不是推送；
    // 但"没有板子就没有 node_id"这条依赖跟四态判据同源，所以 `live` 也在需求里。
    needs: ["live"],
    links: "channel",
  },
  {
    id: "signal-events",
    name: "事件信息流",
    summary: "采集层写的观测事件：断流与恢复，含证据链与局限说明",
    size: { w: 6, h: 5 },
    min: { w: 3, h: 3 },
    source: "live",
    workspace: "site",
    needs: ["live"],
    links: "channel",
  },
  {
    id: "node-status",
    name: "节点状态",
    summary: "接入机与节点的真实连接与能力声明",
    size: { w: 4, h: 4 },
    min: { w: 3, h: 3 },
    source: "api",
    workspace: "site",
    needs: ["nodes"],
    links: "node",
  },
  {
    id: "site-tree",
    name: "场地层级树",
    summary: "场地 → 位置 → 接入机 → 节点，选中即切视图（不停后台）",
    size: { w: 3, h: 6 },
    min: { w: 2, h: 3 },
    source: "api",
    workspace: "site",
    needs: ["hierarchy"],
  },
  {
    id: "channel-activity-heatmap",
    name: "频道活动热图",
    summary: "频道 × 时间的活动格：哪一档在什么时候越过阈值",
    size: { w: 6, h: 4 },
    min: { w: 4, h: 3 },
    source: "api",
    workspace: "site",
    // 活动判据是"桶均值 ≥ 阈值"——同一个 `threshold_db` 生效值，规则插件用它判活动、
    // 这格用它画格，两处不各设一个数。取数口与 rssi-trend 同源，依赖也同源。
    needs: ["live"],
    links: "channel",
  },
  {
    id: "target-inspector",
    name: "目标详情",
    summary: "候选目标的证据、时间线与人工复核（确认 / 否定）",
    size: { w: 6, h: 5 },
    min: { w: 4, h: 4 },
    source: "api",
    workspace: "site",
    // 读的是 /api/targets（按场地的候选目标）。它不订推送、也不必挑一块板——只要有
    // 场地上下文就说得出话，所以挂在 `hierarchy` 这条通路上。
    needs: ["hierarchy"],
    links: "node",
  },
  {
    id: "alert-feed",
    name: "告警信息流",
    summary: "规则引擎推出来等人处置的告警：短语 + 展开说明、判定依据与处置（确认/静默/解决）",
    size: { w: 6, h: 5 },
    min: { w: 4, h: 4 },
    source: "api",
    workspace: "site",
    // 读的是 /api/alerts（按场地的处置窗）。与 target-inspector 同一条口径：不订推送、
    // 不挑板，只要有场地上下文就说得出话，所以也挂在 `hierarchy` 上。
    needs: ["hierarchy"],
    links: "node",
  },
  // 下面三格是**写侧**：原单机控制台那套单模块接入点控制（扫描控制 / 协议控制台 / 原始 I/Q）
  // 搬进站点侧。它们与上面那些读侧格子有两点口径不同，写在数据里而不是只写在组件里：
  // - `needs` 只要 `nodes`：有板子名册就有控制对象。它们不订 `/ws/live`，也不靠层级树——
  //   命令走 `POST /api/nodes/{id}/command`，板子自己回答。
  // - **一格只对一块板下发**（后端每端点一把命令锁）。`nodes` 勾了多块时组件拒绝下发并说清
  //   怎么改，判据在 `components/select.js` 的 `controlTarget`，不在这里再写一份。
  {
    id: "node-controls",
    name: "扫描控制",
    summary: "对一块板下发扫描/增益/滤波/FFT/频道计划与启用位，改完立刻重读板子",
    size: { w: 6, h: 6 },
    min: { w: 4, h: 4 },
    source: "api",
    workspace: "site",
    needs: ["nodes"],
    links: "node",
  },
  {
    id: "node-console",
    name: "协议控制台",
    summary: "一行命令原样发给一块板，板子的回答（含 ERR 原词）与 JSON 都印在日志里",
    size: { w: 6, h: 5 },
    min: { w: 4, h: 3 },
    source: "api",
    workspace: "site",
    needs: ["nodes"],
    links: "node",
  },
  {
    id: "node-rawiq",
    name: "原始 I/Q 快照",
    summary: "对一块板抓一份 RAW IQ：星座、时间波形与客户端 FFT 谱（样本上限由板子报）",
    size: { w: 6, h: 6 },
    min: { w: 4, h: 4 },
    source: "api",
    workspace: "site",
    needs: ["nodes"],
    links: "node",
  },
];

/**
 * 声明出来但**本阶段不可用**的条目。理由与阶段写在数据里，页面上直接呈现。
 * 空着不写才是问题：运营者会以为"这功能坏了"，而不是"这功能还没到"。
 *
 * `workspace` 标的是**这件事归哪一边**：`site` 是这一页要做而还没做，`single-node` 是
 * 它本来就在单机控制台里、平台侧按 C-7 裁定不移植。两者在页面上说的话不一样，混成
 * "不上线"三个字就让运营者分不清"排期没到"和"设计上就没有"。
 *
 * 理由里不写 `xxx.py:573` 那种行号指针：上一次它烂成 573→649 只隔了一次提交。
 * 要指就指**符号名**（`_frame()`、`FRAME_NAMES`），并由 `test/registry.test.mjs` 去后端源文件里
 * 认一遍——符号改名会红，行号腐烂不会。
 */
const CONSOLE_BASE = workspaceById("single-node").base;

export const PLANNED = [
  {
    id: "spectrum-view",
    name: "真实频谱",
    summary: "需要真实频率分辨数据",
    available: false,
    workspace: "site",
    reason:
      "S6 的推送口只发 samples（电平帧）与 event 两类；频谱帧在采集层由 `capture.py` 的 `_frame()` 计入 skipped_frames 后整帧丢弃（名单在 `FRAME_NAMES`），页面这一侧没有那条通路。离散频道的 RSSI 不会冒充连续频谱（规范 §2.3）。P0 阶段 4 的栅格迁移与 `/api/spectrum` 接上的是这一条，组件骨架已经在 `components/spectrum-band.js`",
    stage: "P0 阶段 4",
  },
  {
    id: "waterfall-view",
    name: "真实瀑布图",
    summary: "需要频率—时间—幅度三轴真实采样",
    available: false,
    workspace: "site",
    // 色表这一格在阶段 3.3 已经解决（`vendor/waterfall-lut.json` 与 `web/` 双向对拍），
    // 剩下的还是那句话：这条链上没有频率分辨数据。写清楚哪一半已经好了，
    // 是为了下一阶段的人不必重新查一遍"瀑布图到底缺什么"。
    reason: "瀑布图的三轴要频谱帧或 RAW IQ，而 `/ws/live` 里没有这两类帧——缺的还是数据，不是颜色：256 项色表已由 `vendor/waterfall-lut.json` 与 `web/src/util/canvas.ts` 对拍复用（P0 阶段 3.3），本系统的原始 IQ 通路在原 `web/` 与 Android 端，不在这条链上",
    stage: "P0 阶段 4",
  },
  {
    id: "node-map",
    name: "节点地图",
    summary: "仅在位置数据可靠时启用",
    available: false,
    workspace: "site",
    reason: "系统里没有可靠位置来源，不做按 RSSI 猜坐标那一件事（规范 §2.3）",
    stage: null,
  },
  {
    id: "firmware-flash",
    name: "网页内固件烧录",
    summary: "浏览器直连芯片刷镜像",
    available: false,
    workspace: "single-node",
    reason: `这件事要 Web Serial 独占那块板，经后端代理会抢串口（规范 §2.3 的约束 4），平台侧不做：功能在单机控制台 ${CONSOLE_BASE}，这一页只负责把人送到那一边`,
    stage: null,
  },
  {
    id: "power-meter",
    name: "VTX 功率计",
    summary: "衰减/平滑参数下的功率读数",
    available: false,
    workspace: "single-node",
    reason: `单机侧的第三页读的是这一块板的实时 RAW/电平通路，站点侧没有对应的面板形态（P0 差异表 C-7 裁定按 A：平台侧不做），功能在单机控制台 ${CONSOLE_BASE}`,
    stage: null,
  },
  {
    id: "lap-timing",
    name: "圈速计时 / RotorHazard 节点",
    summary: "计圈面板与 RotorHazard 服务端读",
    available: false,
    workspace: "single-node",
    reason: `计圈面板与 RotorHazard 那一路平台侧不做，留在单机控制台 ${CONSOLE_BASE} 与 serve.mjs（P0 差异表 #18 按 C-7 裁定；规范 §2.3 的约束 9 要求 USB/UART/RH 这条链一行都不许删，但不是把它搬进这一页）。平台侧只要求阶段 7 做到"切换态可见"`,
    stage: null,
  },
];

export const KNOWN_IDS = new Set(COMPONENTS.map((entry) => entry.id));

/**
 * `source` 这个词怎么说给人听。词表放这里是因为 **`source` 的取值只有注册表知道**，
 * 页面拿它渲染时不该再自己拆一遍三元——漏掉一个取值就会把 REST 那格说成"实时推送"。
 * 认不出的来源原样吐出来：那是这次发布少了一个分支的证据，不该被显示成一个已知的词。
 */
const SOURCE_WORD = { live: "实时推送", api: "REST 拉取" };

export const sourceWord = (source) => SOURCE_WORD[source] || `未知来源 ${source}`;

/**
 * `workspace` 这个词怎么说给人听。名字只有 `workspaces.js` 那一份：在这里再抄一遍
 * "Site Monitoring"，两处就会在下次改措辞时分家。
 * 认不出的取值原样带出，同 `sourceWord` 那条纪律。
 */
export const workspaceWord = (workspace) => {
  const entry = workspaceById(workspace);
  return entry ? `${entry.name}（${entry.zh}）` : `未知工作区 ${workspace}`;
};

export function component(id) {
  return COMPONENTS.find((entry) => entry.id === id) || null;
}

/** 库里每一行的可点性与理由：`facts.capabilities` 是节点能力集合（今天来自 /api/nodes）。 */
export function library(facts = { capabilities: [] }) {
  const ready = new Set(facts.capabilities || []);
  const live = COMPONENTS.map((entry) => {
    const missing = entry.needs.filter((need) => !ready.has(need));
    return {
      ...entry,
      kind: "live",
      available: missing.length === 0,
      reason: missing.length ? `缺少能力：${missing.join("、")}` : null,
    };
  });
  return live.concat(PLANNED.map((entry) => ({ ...entry, kind: "planned", size: { w: 4, h: 3 } })));
}
