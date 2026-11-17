/**
 * 协议控制台：原单机控制台那条命令行（`web/src/views/debug.ts` 的 `Console`）搬到站点侧，
 * **一格只对一块板发**，板子的回答原样印出来。
 *
 * 与单机侧的四点不同，都是多点接入逼出来的：
 *
 * - **对象常驻可见**（`controlHead`）：板名/节点号/接入机/传输方式印在同一处，`nodes` 没勾时
 *   给一个板下拉。命令行最容易出的事故是"以为在写 A 板，其实写的是 B 板"。
 * - **日志按板分开看**：缓存里存的是这一格发过的全部命令（上限 `LOG_CAP`），画的时候只画
 *   当前这块板的那些。切板不该把上一块板的回答留在屏幕上。
 * - **不自动重读**：单机侧发完设置命令会顺手 `refreshStatus()`，这里不做——读板要占串口，
 *   占着串口 RotorHazard 就起不来（R21）。改过之后 `controlHead` 会说"读数还是改之前的"，
 *   要不要重读由运营者点。
 * - **板子的拒绝不是故障**：`ERR …` 是板子说的话，进日志、加一句人话注解，但原词一个不改。
 *
 * 这一格**不画 RAW IQ 图**：三个格子各有一份自己的读数缓存（`control.js`），在这里抓到的
 * 快照不会跑到「原始 I/Q」那一格里去。所以收到快照时明说"去那一格抓"，而不是假装图在下面。
 */

import { controlBlocked, controlHead, createControl, LOG_CAP } from "./control.js";
import { empty, fmtClock } from "./parts.js";
import { controlTarget } from "./select.js";

export const plugin_id = "node-console";

/**
 * 裸查询快捷钮。每一条都在固件里真答（`commands.c`），词表与后端 `node_control.QUERY_PREFIX`
 * 同一套——那边漏一条就是白等一个超时，这边多一条就是点了没回答。
 * `SYNC` 不在这里：它要带一个参数（`SYNC <字>`），裸发只会得到 `ERR command`。
 */
const QUICK = [
  "INFO",
  "STATUS?",
  "CONFIG?",
  "PROFILE?",
  "GAIN?",
  "TIMING?",
  "SPURS?",
  "ENABLE?",
  "PROTOCOL?",
  "METRICS?",
  "GPIO?",
  "LEVELFILTER?",
  "FFTTYPE?",
  "PHYIDLE?",
];

/**
 * `ERR <词>` 的人话注解。**原词保留在日志里**，这里只补一句"它为什么这么说"，
 * 措辞对着固件的判据写（`commands.c`），不是对着猜。认不出的词不加注——
 * 编一句解释比不解释更贵。
 */
const ERR_NOTE = {
  command: "固件不认识这条命令",
  command_length: "超过固件行缓冲（126 字符）",
  busy: "扫描器正忙：等这一轮做完再发",
  noscanner: "这块板是中继，扫描器在 head 那一侧",
  head_down: "中继连不上 head：这条命令没人接",
  rh_mode: "板子现在跑 RotorHazard 协议，只认它允许的那几条命令",
  rh_only: "这条命令只在 RotorHazard 协议下有效",
  uart_port: "这类帧只走 USB 口：当前接入方式带不了它",
  uart_rh: "RotorHazard 正占着电台（UART 链路还活着）",
  rawiq_args: "LO 要是板子调谐步长的整数倍，样本数要在 16…rawiq_max 之间",
  gain_range: "增益索引超出板子报的 gain_max",
  channels: "频道计划解析不了：名字要在板子的频段表里，且不超过 8 档",
  spurs_full: "杂散表已满（16 条）",
  reply_length: "回答超出固件的回复缓冲：问的东西太多了",
  gpio_pin: "这个 GPIO 号板子上没有",
  gpio_state: "GPIO 状态只认 NONE / LOW / HIGH",
  save: "写 NVS 失败",
  reset: "复位失败",
  ffttype: "FFT 类型名固件不认识",
};

/** `ERR foo bar` → `foo`。板子偶尔在词后面带参数，注解只按第一个词查。 */
const errWord = (reply) => {
  const match = /^ERR\s+([A-Za-z_]+)/.exec(String(reply || ""));
  return match ? match[1] : null;
};

export function create(rt, deps) {
  const { h, ref } = rt;

  return {
    name: "NodeConsole",
    props: { widget: { type: Object, required: true } },
    setup(props) {
      const widget = props.widget;
      const ctl = createControl(rt, deps, widget);
      const draft = ref("");
      /** 上下键翻的历史**按板分开**：在 B 板翻出 A 板的命令，回车就是写错板。 */
      const history = {};
      let seenNode = null;

      const writable = () => deps.context.state.instanceRole !== "aggregator";
      const locked = () => !writable() || ctl.busy.value;

      function historyOf(nodeId) {
        if (!history[nodeId]) history[nodeId] = { list: [], pos: 0 };
        return history[nodeId];
      }

      async function submit(nodeId, text) {
        const line = String(text).trim();
        if (!line) return;
        const hist = historyOf(nodeId);
        hist.list.push(line);
        hist.pos = hist.list.length;
        draft.value = "";
        await ctl.send(nodeId, line);
      }

      function recall(nodeId, step) {
        const hist = historyOf(nodeId);
        const next = Math.min(hist.list.length, Math.max(0, hist.pos + step));
        hist.pos = next;
        draft.value = next === hist.list.length ? "" : hist.list[next];
      }

      function sync() {
        const target = controlTarget(deps, widget);
        const id = target.board ? target.board.node_id : null;
        if (id === seenNode) return;
        seenNode = id;
        if (id !== null) ctl.read(id);
      }

      rt.onMounted(sync);
      rt.onUpdated(sync);

      return () => {
        deps.signal.tick.value;
        ctl.busy.value;
        ctl.failure.value;
        ctl.boards.value;
        ctl.log.value;
        draft.value;

        const target = controlTarget(deps, widget);
        if (!target.board) return h("div", { class: "node-control" }, [controlBlocked(rt, target)]);

        const board = target.board;
        const nodeId = board.node_id;
        const disabled = locked();
        const entries = ctl.log.value.filter((entry) => entry.node_id === nodeId);

        const head = controlHead(rt, deps, {
          board,
          target,
          ctl,
          actions: [
            h("button", {
              class: "btn",
              type: "button",
              disabled,
              title: "STATUS?：把板子现在的设置读回来",
              onClick: () => ctl.read(nodeId),
            }, "重读板子"),
          ],
        });

        return h("div", { class: "node-control node-console" }, [
          head,
          h("p", { class: "ctl-quick" }, QUICK.map((command) =>
            h("button", {
              class: "btn btn--quick",
              type: "button",
              disabled,
              title: `发 ${command}`,
              onClick: () => submit(nodeId, command),
            }, command)
          )),
          h("div", { class: "console-row" }, [
            h("input", {
              class: "console-input",
              type: "text",
              value: draft.value,
              disabled,
              placeholder: "一行命令，例如 TIMING? / GAIN MANUAL 45 / SPURS?",
              title: "原样发给板子（只去首尾空白）：不做大小写归一，也不替它补参数",
              onInput: (event) => { draft.value = event.target.value; },
              onKeydown: (event) => {
                if (event.key === "Enter") submit(nodeId, draft.value);
                else if (event.key === "ArrowUp") { event.preventDefault(); recall(nodeId, -1); }
                else if (event.key === "ArrowDown") { event.preventDefault(); recall(nodeId, 1); }
              },
            }),
            h("button", {
              class: "btn btn--primary",
              type: "button",
              disabled: disabled || !draft.value.trim(),
              onClick: () => submit(nodeId, draft.value),
            }, "发送"),
          ]),
          entries.length
            ? h("ul", { class: "console-log" }, entries.map((entry) => line(rt, entry)))
            : empty(rt, "这一格还没对这块板发过命令", "点上面的查询钮，或自己打一行"),
          h("p", { class: "note" }, `日志只留最近 ${LOG_CAP} 条（这一格发过的全部命令，按板筛着看）；板子的原话一个不改，注解只是加注。`),
          healthNote(rt, deps),
        ]);
      };
    },
  };
}

/** 一条问答。失败与成功画在同一行里，因为"发了什么"和"它答了什么"必须一起读。 */
function line(rt, entry) {
  const { h } = rt;
  const word = entry.ok ? null : errWord(entry.reply);
  const note = word ? ERR_NOTE[word] : null;
  const failure = entry.ok
    ? null
    : `失败（${entry.reason || "board_refused"}）：${entry.detail || entry.message || entry.reply || "板子没有回答"}`;
  return h("li", { class: ["console-line", entry.ok ? null : "is-bad"] }, [
    h("span", { class: "console-at" }, fmtClock(entry.at, "UTC")),
    h("span", { class: "console-tx" }, `> ${entry.command}`),
    h("span", { class: "console-rx" }, entry.reply === null || entry.reply === undefined
      ? `< （没有文本回复${entry.rawiq ? "，收到 RAW IQ 快照" : ""}）`
      : `< ${entry.reply}`),
    failure ? h("span", { class: "warn" }, failure) : null,
    note ? h("span", { class: "note" }, `ERR ${word}：${note}`) : null,
    entry.rawiq
      ? h("span", { class: "note" }, `RAW IQ 快照 ${entry.rawiq.n} 点已收到，但这一格不画图：去「原始 I/Q」那一格抓，图在那边`)
      : null,
    entry.data
      ? h("details", { class: "console-json" }, [
          h("summary", {}, "JSON"),
          h("pre", {}, JSON.stringify(entry.data, null, 2)),
        ])
      : null,
    entry.ep
      ? h("span", { class: "src" }, `端点 ${entry.ep}${entry.elapsed_ms === null || entry.elapsed_ms === undefined ? "" : ` · ${entry.elapsed_ms} ms`}`)
      : null,
  ]);
}

/**
 * 命令通路的账（`/api/health` 的 `control` 一节）。**这份是页面启动时读到的快照**，
 * 不是实时数：`context.boot()` 只取一次 health，所以措辞里必须带着"启动时"，
 * 否则运营者会拿一个十分钟前的 holders 数去判断"现在有没有手占着串口"。
 */
function healthNote(rt, deps) {
  const { h } = rt;
  const health = deps.context.state.health;
  const control = health && health.control;
  if (!control) return null;
  const text = `命令通路（页面启动时读到的 /api/health）：${control.enabled ? "开" : "关"} · 握着 ${control.holders} 块板 · 发过 ${control.commands} 条 · 超时 ${control.timeouts} · 失败 ${control.failures} · 闲 ${control.idle_release_s} s 自动交还`;
  return h("p", { class: control.enabled ? "note" : "warn" }, [
    text,
    control.last_error ? h("span", { class: "warn" }, ` · 上一条错：${control.last_error}`) : null,
  ]);
}