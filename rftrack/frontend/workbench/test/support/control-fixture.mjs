/**
 * 写侧三格（扫描控制 / 协议控制台 / 原始 I/Q）的夹具：**假的只有板子与那条 HTTP 通路**。
 *
 * 三件事有意为之：
 *
 * 1. 板子的回答是**手写的固件原话**，照着 `firmware/src/proto/commands.c` 的 `send_status` /
 *    `send_profile` / `INFO` 抄键与量纲，不是从命令表推出来的。推出来的回答会跟着被测代码
 *    一起错——`QUERY_PREFIX` 漏一条、前端读错一个键，两边照样对得上，测试就一直绿。
 * 2. `api` 是**真的 `createApi`**，只有 `fetchImpl` 是假的。于是 URL 形状、`{detail:{reason}}`
 *    → `ApiError.reason` 的归一、调用账都走真路；假的那一层只负责"板子答了什么"。
 * 3. 两块板的读数**故意不一样**（增益 45 vs 12、上限 16380 vs 4096、在扫 vs 已停）。
 *    "A 板的数字不许出现在 B 板的标题下"这条判据，只有两份不同的数才验得出来。
 *
 * 结果对象的形状照抄后端 `services/node_control.py::_result`：`ERR …` 是 200 带 `ok:false`，
 * `data` 取回复里第一个 `{` 之后那段（`json_payload`），RAWIQ 成功时没有文本回复。
 */

import { createApi } from "../../api.js";
import * as registry from "../../registry.js";
import { connected } from "./live-fixture.mjs";
import { fakeRt, fakeWidget, stubComputedStyle } from "./vue-fixture.mjs";

// --- 板子的原话 -----------------------------------------------------------------

/** 板 R1：S3 那一类，在扫、八档全启用、手动增益 45、rawiq 上限 16380。 */
export const STATUS_R1 =
  'STATUS {"protocol":"MONITOR","profile":"fpv58","running":1,"enabled":255,"mode":"GROUP",' +
  '"capture_samples":1024,"nfft":256,"segments":4,"rate_code":1,"filter":-1,"iq_corr":1,' +
  '"settle_us":300,"tskip":0,"gain_mode":"MANUAL","gain":45,"gain_max":82,"rawiq_max":16380,' +
  '"recal":"CACHE","metrics":"FULL","rssi_hz":100000,"search_hz":600000,"dc_guard":0,' +
  '"ema_ms":40,"clear_ms":2000,"signal_db":6.0,"avg_shift":2,"spec_every":0,"spec_bins":256,' +
  '"spec_span_hz":20000000,"spec_detector":1,"fft":"RECT","level_filter":"EMA","throttle_hz":50,' +
  '"phy_idle":0,"phy_idle_min_us":0,"plan":[' +
  '{"lo_khz":5658000,"ch":[0,1,2,3],"offset_hz":[0,37000,74000,111000]},' +
  '{"lo_khz":5806000,"ch":[4,5,6,7],"offset_hz":[0,37000,74000,111000]}]}';

export const CONFIG_R1 = STATUS_R1.replace(/^STATUS /, "CONFIG ");

export const PROFILE_R1 =
  'PROFILE {"name":"fpv58","description":"5.8 GHz FPV","target":"esp32s3",' +
  '"band_khz":[5645000,5945000],"base_khz":5645000,"equiv_base_khz":5645000,"scale":1.000000,' +
  '"rate_hz":12000000,"usable_hz":10000000,"passband_hz":13000000,"rssi_hz":100000,' +
  '"search_hz":600000,"channels":[' +
  '{"id":0,"name":"R1","khz":5658000},{"id":1,"name":"R2","khz":5695000},' +
  '{"id":2,"name":"R3","khz":5732000},{"id":3,"name":"R4","khz":5769000},' +
  '{"id":4,"name":"R5","khz":5806000},{"id":5,"name":"R6","khz":5843000},' +
  '{"id":6,"name":"R7","khz":5880000},{"id":7,"name":"R8","khz":5917000}]}';

export const INFO_R1 =
  "INFO widscan58|esp32s3|6|0.6.0|2026-11-01T02:03:04Z|ESP32-S3 SPH 8.1.2|aa:bb:cc:dd:ee:01|4321";

/**
 * 同一块板刚上电、还没扫过第一轮的样子：`plan` 是空的。`scanner_get_plan` 交的是共享快照
 * （`firmware/src/scan/scanner.c:148`），第一轮之前里面没有跳——"用当前档的 LO"这时只能
 * 退到 PROFILE? 的中心频率，那条退路的判据就靠这份回答才验得出来。
 */
export const STATUS_FRESH = `STATUS ${JSON.stringify({ ...JSON.parse(STATUS_R1.slice("STATUS ".length)), running: 0, plan: [] })}`;

/** 板 R2：C5 那一类，已停、只启用第 0 档、AGC、上限 4096，而且计划是**缩放档**（名字这张表不认识）。 */
export const STATUS_R2 =
  'STATUS {"protocol":"MONITOR","profile":"scaled24","running":0,"enabled":1,"mode":"SINGLE",' +
  '"capture_samples":256,"nfft":64,"segments":4,"rate_code":0,"filter":60,"iq_corr":0,' +
  '"settle_us":150,"tskip":3,"gain_mode":"HARDWARE","gain":12,"gain_max":63,"rawiq_max":4096,' +
  '"recal":"EVERY","metrics":"RSSI","rssi_hz":50000,"search_hz":300000,"dc_guard":0,' +
  '"ema_ms":80,"clear_ms":5000,"signal_db":6.0,"avg_shift":1,"spec_every":4,"spec_bins":512,' +
  '"spec_span_hz":10000000,"spec_detector":0,"fft":"HANN","level_filter":"MEDIAN","throttle_hz":20,' +
  '"phy_idle":1,"phy_idle_min_us":200,"plan":[{"lo_khz":5740000,"ch":[0],"offset_hz":[0]}]}';

export const PROFILE_R2 =
  'PROFILE {"name":"scaled24","description":"2.4 GHz scaled","target":"esp32c5",' +
  '"band_khz":[2400000,2483000],"base_khz":2400000,"equiv_base_khz":5645000,"scale":0.425000,' +
  '"rate_hz":8000000,"usable_hz":6000000,"passband_hz":8000000,"rssi_hz":50000,' +
  '"search_hz":300000,"channels":[{"id":0,"name":"X1","khz":2412000}]}';

export const INFO_R2 =
  "INFO widscan58|esp32c5|6|0.6.0|2026-11-01T02:03:04Z|ESP32-C5 SPH 8.1.2|aa:bb:cc:dd:ee:02|99";

export const R1_REPLIES = { "STATUS?": STATUS_R1, "CONFIG?": CONFIG_R1, "PROFILE?": PROFILE_R1, INFO: INFO_R1 };
export const R2_REPLIES = { "STATUS?": STATUS_R2, "PROFILE?": PROFILE_R2, INFO: INFO_R2 };

/** 名册里的两行：形状照抄 `GET /api/nodes`（能力平铺，没有 `limits` 子对象）。 */
export const BOARD_R1 = {
  node_id: 30, gateway_id: 20, site_id: 1, display_name: "板 R1", status: "active", stops_rh: true,
  transport: { kind: "uart", addr: "/dev/ttyAMA0" }, fw_version: "0.6.0", protocol_version: 6,
  last_seen: "2026-10-29T04:00:00", declaration_error: null,
  capabilities: { levels: { supported: true }, rawiq: { supported: true, max_samples: 16380 } },
};

export const BOARD_R2 = {
  ...BOARD_R1,
  node_id: 31, display_name: "板 R2", stops_rh: false,
  transport: { kind: "usb", addr: "/dev/ttyUSB0" },
  capabilities: { levels: { supported: true }, rawiq: { supported: false, reason: "undeclared" } },
};

export const GATEWAY = { gateway_id: 20, relay_id: 10, site_id: 1, name: "桥 A", base_url: "http://192.168.99.50:8080" };

/** `/api/health` 的 `control` 一节（`NodeControl.status()`）：页面启动时读到的那一份快照。 */
export const HEALTH = {
  instance_role: "site",
  control: { enabled: true, holders: 1, commands: 7, timeouts: 0, failures: 1, last_error: null, idle_release_s: 30 },
};

// --- 假板 -----------------------------------------------------------------------

/**
 * 一块假板。`replies` 的值可以是：
 *
 * - 字符串：板子的文本回复（`ERR …` 会被判成 `ok:false`，与后端同一条规则）；
 * - `{rawiq}`：抓成功，没有文本回复；
 * - `{reply:null}`：成功但没话说；
 * - `{error:{status,reason,detail}}`：后端拒绝下发（409/504/…），走 HTTP 错误那条路。
 *
 * `manual:true` 时每次调用都挂起，由测试 `settle()` 决定什么时候答、按什么顺序答——
 * "一格之内命令串行"这条判据只有把回答顺序拧过来才验得出来。
 */
export function fakeBoard({ replies = R1_REPLIES, rawiq = null, ep = "/dev/ttyAMA0", elapsedMs = 12, manual = false } = {}) {
  const sent = [];
  const waiting = [];

  function answerOf(command) {
    if (Object.hasOwn(replies, command)) return replies[command];
    if (command.startsWith("RAWIQ ")) return rawiq === null ? { reply: null } : { rawiq };
    return "OK";
  }

  function result(nodeId, command, answer) {
    if (answer && answer.error) {
      const { status = 409, reason, detail = null } = answer.error;
      return { http: { status, body: { detail: { reason, detail } } } };
    }
    const reply = answer && typeof answer === "object" ? (answer.reply ?? null) : answer;
    const snapshot = answer && typeof answer === "object" ? (answer.rawiq ?? null) : null;
    const start = typeof reply === "string" ? reply.indexOf("{") : -1;
    let data = null;
    if (start >= 0) {
      try {
        data = JSON.parse(reply.slice(start));
      } catch {
        data = null;
      }
    }
    const out = {
      node_id: nodeId, gateway_id: GATEWAY.gateway_id, ep, command,
      ok: !(typeof reply === "string" && reply.startsWith("ERR")),
      reply, data, rawiq: snapshot, elapsed_ms: elapsedMs,
    };
    if (command.split(" ")[0] === "RAWIQ" && snapshot === null) {
      out.ok = false;
      out.reason = "rawiq_incomplete";
      out.detail = "RAWIQ 分块缺了中间一块，整快照已作废：重抓一次";
    }
    return { http: { status: 200, body: out } };
  }

  return {
    sent,
    waiting,
    ask(nodeId, command) {
      sent.push(command);
      const answer = answerOf(command);
      if (!manual) return Promise.resolve(result(nodeId, command, answer));
      return new Promise((resolve) => { waiting.push({ command, nodeId, answer, resolve }); });
    },
    /** 答掉挂起中的第 `index` 条（默认最早那条）。回它的命令，供顺序断言。 */
    settle(index = 0) {
      const row = waiting.splice(index, 1)[0];
      if (!row) throw new Error("没有挂起的命令可答：测试的前提已经不成立");
      row.resolve(result(row.nodeId, row.command, row.answer));
      return row.command;
    },
  };
}

/**
 * 一个已知频率的复正弦快照：`bin` 是它在 1024 点 FFT 里的格子号，`amp` 是**传输计数**幅值。
 * 满量程（`amp = 512 / 2^shift`）时它自己那一格该读 0 dBFS——这是判据的来源，不是算出来的。
 */
export function toneSnapshot({ n = 4096, loKhz = 5658000, rateHz = 12_000_000, shift = 2, bin = 100, amp = 128, conjugated = false } = {}) {
  const i = [];
  const q = [];
  for (let j = 0; j < n; j += 1) {
    const phase = (2 * Math.PI * bin * j) / 1024;
    i.push(Math.round(amp * Math.cos(phase)));
    q.push(Math.round(amp * Math.sin(phase)) * (conjugated ? -1 : 1));
  }
  return { loKhz, rateHz, n, shift, conjugated, i, q };
}

// --- 一套依赖 -------------------------------------------------------------------

const flush = () => new Promise((resolve) => setImmediate(resolve));

/**
 * `options`：
 * - `boards`：名册（默认两块板）；`nodeId`：四级选择器选中的那块；
 * - `nodes`：这一格 `nodes` 配置勾了哪几块（写侧勾多块就该拒绝）；
 * - `fakes`：`{node_id: fakeBoard}`，名册里没有假板的节点一律 `not_found`（与后端 404 同一句话）；
 * - `role`：实例角色（`aggregator` 只读）；`health`：启动时那份 health；
 * - `frames`：连上之后喂进实时环的帧；`link`：`store.state.link`（联动那一档）。
 */
export function controlEnv(options = {}) {
  stubComputedStyle();
  const rt = fakeRt();
  const widget = fakeWidget(options.plugin ?? "node-controls", options.widget);
  const boards = options.boards ?? [BOARD_R1, BOARD_R2];
  const nodeId = options.nodeId === undefined ? (boards.length ? boards[0].node_id : null) : options.nodeId;
  const fakes = options.fakes ?? {
    30: fakeBoard(),
    31: fakeBoard({ replies: R2_REPLIES, ep: "/dev/ttyUSB0", elapsedMs: 31 }),
  };
  const calls = [];

  const api = createApi({
    base: "",
    fetchImpl: async (url, init) => {
      const match = /^\/api\/nodes\/(\d+)\/command$/.exec(url);
      if (!match || init.method !== "POST") throw new Error(`夹具只认命令端点，收到 ${init.method} ${url}`);
      const id = Number(match[1]);
      const { command } = JSON.parse(init.body);
      calls.push({ nodeId: id, command });
      const board = fakes[id];
      if (!board) {
        return {
          ok: false, status: 404,
          json: async () => ({ detail: { reason: "not_found", detail: "库里没有这个节点或它所属的接入机" } }),
        };
      }
      const { http } = await board.ask(id, command);
      return { ok: http.status < 300, status: http.status, json: async () => http.body };
    },
  });

  const env = connected({ site: options.site ?? 1, staleAfterS: options.staleAfterS ?? 15 });
  for (const frame of options.frames ?? []) env.net.sockets[0].sent(frame);

  const store = {
    state: { link: options.link ?? null, widgets: [], selection: null },
    effectiveOf: (row, key) => ({ ...(options.effective || {}), ...(row.effective || {}) })[key] ?? null,
    describe: (error) => ({ reason: error.reason || "unknown", message: error.message || String(error) }),
  };
  const context = {
    state: {
      sites: [], relays: [], gateways: options.gateways ?? [GATEWAY], nodes: boards, scopeNodes: boards,
      scopeGateways: [], siteId: 1, relayId: 10, gatewayId: GATEWAY.gateway_id, nodeId,
      instanceRole: options.role ?? "site", health: options.health === undefined ? HEALTH : options.health,
      loaded: { hierarchy: true, nodes: true }, loading: false, error: null,
      ...(options.contextState || {}),
    },
    gateway: () => context.state.gateways.find((row) => row.gateway_id === context.state.gatewayId) || null,
    node: () => context.state.nodes.find((row) => row.node_id === context.state.nodeId) || null,
    lag: () => null,
  };
  const signal = { tick: { value: 0 }, bump: () => { signal.tick.value += 1; } };
  const actions = {
    selectNode(id) {
      calls.push({ action: "selectNode", id });
      context.state.nodeId = Number(id);
      store.state.link = { kind: "node", id: Number(id) };
      signal.bump();
    },
    ...(options.actions || {}),
  };
  const deps = { store, context, live: env.live, actions, signal, registry, api };

  return {
    rt, deps, widget, api, calls, fakes, store, context, actions, signal,
    live: env.live, time: env.time, timers: env.timers, socket: env.net.sockets[0],
    flush,
    /** 挂一次：`setup()` 拿 props，挂载与更新钩子都跑一遍（切板判据住在 `onUpdated` 里）。 */
    mount(factory) {
      const component = factory(rt, deps);
      const tree = component.setup({ widget });
      rt.hooks.mounted.forEach((fn) => fn());
      rt.hooks.updated.forEach((fn) => fn());
      return tree;
    },
    /** 再渲染一次：改了 context/store 之后要手动泵，假 Vue 不会自己响应。 */
    update() {
      rt.hooks.updated.forEach((fn) => fn());
    },
  };
}