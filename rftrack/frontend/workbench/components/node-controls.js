/**
 * 扫描控制：原单机控制台那张控制卡（`web/src/views/controls.ts`）搬到站点侧，
 * **一格只对一块板下发**，控件与固件命令一一对应，改完立刻重读 `STATUS?`。
 *
 * 搬到多点之后多出来的四件事，也是这一格与单机侧真正的区别：
 *
 * - **对象常驻可见**：标题行印着板名/节点号/接入机/传输方式，`nodes` 没勾时还给一个板下拉，
 *   切板即切控制对象。勾了多块则拒绝下发并说清怎么改（`select.js` 的 `controlTarget`）。
 * - **读数按板分开**：A 板的增益不会出现在 B 板的标题下（`control.js` 的缓存）。
 * - **不轮询**：读板要占串口，占着串口 RotorHazard 就起不来。所以只在挂载/切板/下发后/
 *   点"重读"时读，读数旧了就把旧说出来。
 * - **频道计划从板子读回**（`PROFILE?` 的 `channels`），不靠浏览器里那份旧偏好：
 *   站点侧一块板可能被别的运营者改过。
 *
 * 单机侧那两项**没有搬**，理由写在这里而不是留一个死旋钮：
 * - "Display dB range (UI only)" 是原控制台图表的显示范围，不是板子的设置；这一页的图表
 *   纵轴由 `chart.js` 按数据定域，没有可接的落点。
 * - GPIO / 网络配置对话框：命令本身（`GPIO …`、`NET …`）在 `node-console` 里原样可发，
 *   这一格不重复造一套表单。
 */

import { BANDS, MAX_CHANNELS, channelsCommand, normalizePlan, planFromProfile } from "../channel-plan.js";
import { controlBlocked, controlHead, createControl } from "./control.js";
import { empty } from "./parts.js";
import { controlTarget } from "./select.js";

export const plugin_id = "node-controls";

const FFT_SIZES = [64, 128, 256, 512, 1024];
const SPEC_BINS = [64, 128, 256, 512, 1024];
const TSKIP_BITS = [
  [1, "只调 PLL"],
  [2, "跳过 RX 通路"],
  [4, "跳过增益"],
];
/** 勾满时的提示留这么久，与单机侧的频道对话框一致。 */
const HINT_MS = 1800;

export function create(rt, deps) {
  const { h, ref } = rt;

  return {
    name: "NodeControls",
    props: { widget: { type: Object, required: true } },
    setup(props) {
      const widget = props.widget;
      const ctl = createControl(rt, deps, widget);
      /** 编辑中的频道计划与拖动中的增益，都**按节点分开**：切板不该把上一块板的草稿带过去。 */
      const planDraft = ref({});
      const gainDraft = ref({});
      const planNote = ref("");
      let seenNode = null;
      let hintTimer = null;

      const writable = () => deps.context.state.instanceRole !== "aggregator";
      const locked = () => !writable() || ctl.busy.value;

      function ask(text) {
        // 没有 confirm 的环境（Node 单测、被禁用的弹窗）一律当成"没确认"：
        // 重启与进烧录模式都是不可逆的，宁可不动。
        return typeof globalThis.confirm === "function" ? globalThis.confirm(text) : false;
      }

      async function apply(nodeId, command, { profile = false } = {}) {
        const answer = await ctl.send(nodeId, command);
        if (answer && answer.ok) await ctl.read(nodeId, { profile });
        return answer;
      }

      /** 挂载与切板时读一次。`onUpdated` 里判"对象换没换"，所以不用定时器盯着板子。 */
      function sync() {
        const target = controlTarget(deps, widget);
        const id = target.board ? target.board.node_id : null;
        if (id === seenNode) return;
        seenNode = id;
        if (id !== null) ctl.read(id, { profile: true });
      }

      function flash(text) {
        planNote.value = text;
        if (hintTimer !== null) widget.lease.clearTimeout(hintTimer);
        hintTimer = widget.lease.setTimeout(() => {
          planNote.value = "";
          hintTimer = null;
        }, HINT_MS);
      }

      function selectionOf(nodeId) {
        const draft = planDraft.value[nodeId];
        if (draft) return draft;
        const profile = ctl.boardOf(nodeId).profile;
        return profile ? planFromProfile(profile).known : [];
      }

      function setSelection(nodeId, names) {
        planDraft.value = { ...planDraft.value, [nodeId]: names };
      }

      function toggleChannel(nodeId, name) {
        const current = selectionOf(nodeId);
        if (current.includes(name)) {
          setSelection(nodeId, current.filter((row) => row !== name));
          return;
        }
        if (current.length >= MAX_CHANNELS) {
          flash(`计划已满（最多 ${MAX_CHANNELS} 档）：先去掉一档`);
          return;
        }
        setSelection(nodeId, [...current, name]);
      }

      rt.onMounted(sync);
      rt.onUpdated(sync);

      return () => {
        deps.signal.tick.value;
        ctl.busy.value;
        ctl.failure.value;
        ctl.boards.value;
        planDraft.value;
        gainDraft.value;
        planNote.value;

        const target = controlTarget(deps, widget);
        if (!target.board) {
          return h("div", { class: "node-control" }, [controlBlocked(rt, target)]);
        }

        const board = target.board;
        const nodeId = board.node_id;
        const cached = ctl.boardOf(nodeId);
        const st = cached.status;
        const disabled = locked();

        const head = controlHead(rt, deps, {
          board,
          target,
          ctl,
          actions: [
            h("button", {
              class: "btn",
              type: "button",
              disabled,
              title: "STATUS? + PROFILE?：把板子现在的设置与频道计划读回来",
              onClick: () => ctl.read(nodeId, { profile: true }),
            }, "重读板子"),
            st
              ? h("button", {
                  class: st.running ? "btn" : "btn btn--primary",
                  type: "button",
                  disabled,
                  title: st.running ? "SCAN STOP" : "SCAN START",
                  onClick: () => apply(nodeId, st.running ? "SCAN STOP" : "SCAN START"),
                }, st.running ? "停止扫描" : "开始扫描")
              : null,
            st ? h("span", { class: `chip chip--${st.running ? "active" : "idle"}` }, st.running ? "在扫" : "已停") : null,
          ],
        });

        if (!st) {
          return h("div", { class: "node-control" }, [
            head,
            empty(
              rt,
              "还没读到这块板的状态",
              ctl.busy.value ? "正在读……" : "点「重读板子」取回 STATUS?；读不到就不画控件，免得把猜出来的值当成板子的设置"
            ),
          ]);
        }

        const ui = { rt, deps, nodeId, st, cached, disabled, apply, ctl, gainDraft, planDraft, selectionOf, setSelection, toggleChannel, flash, planNote, ask };
        return h("div", { class: "node-control" }, [
          head,
          planSection(ui),
          group(ui, "射频与扫描", [
            modeRow(ui),
            gainRow(ui),
            filterRow(ui),
            fftRow(ui),
            metricsRow(ui),
          ]),
          group(ui, "窗口与平滑", [winRow(ui), emaRow(ui)]),
          group(ui, "显示频谱", [specRow(ui)]),
          group(ui, "校准与调谐", [recalRow(ui), settleRow(ui), tskipRow(ui), iqRow(ui)]),
          group(ui, "设备", [deviceRow(ui)]),
        ]);
      };
    },
  };
}

// --- 控件小件 -------------------------------------------------------------------

function group(ui, title, rows) {
  return ui.rt.h("div", { class: "ctl-group" }, [ui.rt.h("h3", { class: "ctl-group-title" }, title), ...rows]);
}

function row(ui, label, title, ...children) {
  const { h } = ui.rt;
  return h("div", { class: "ctl" }, [
    h("label", { class: "ctl-label", title: title || null }, label),
    h("div", { class: "ctl-row" }, children),
  ]);
}

/** 一组互斥按钮。`current` 认不出时谁都不亮——不替板子挑一个"应该是这个"。 */
function seg(ui, options, current, onPick) {
  const { h } = ui.rt;
  return h("span", { class: "seg" }, options.map(([value, label]) =>
    h("button", {
      class: ["btn", value === current ? "on" : null],
      type: "button",
      disabled: ui.disabled,
      onClick: () => onPick(value),
    }, label)
  ));
}

function number(ui, { value, min, max, step, width = 72, onChange, title = null }) {
  return ui.rt.h("input", {
    class: "ctl-num",
    type: "number",
    min: String(min),
    max: String(max),
    step: String(step),
    style: `width:${width}px`,
    value: value === null || value === undefined ? "" : String(value),
    disabled: ui.disabled,
    title,
    onChange: (event) => onChange(Number(event.target.value)),
  });
}

function select(ui, { value, options, onChange, title = null }) {
  return ui.rt.h("select", {
    class: "ctl-select",
    value: String(value),
    disabled: ui.disabled,
    title,
    onChange: (event) => onChange(event.target.value),
  }, options.map(([v, label]) => h_option(ui.rt, v, label, String(v) === String(value))));
}

const h_option = (rt, value, label, selected) => rt.h("option", { value: String(value), selected }, label);

// --- 频道计划 -------------------------------------------------------------------

function planSection(ui) {
  const { h } = ui.rt;
  const { nodeId, st, cached } = ui;
  const selection = ui.selectionOf(nodeId);
  const plan = planFromProfile(cached.profile);
  const full = selection.length >= MAX_CHANNELS;

  const grid = h("table", { class: "plan-chart" }, [
    h("thead", [h("tr", [h("th"), ...BANDS[0].mhz.map((_, j) => h("th", `CH${j + 1}`))])]),
    h("tbody", BANDS.map((band) =>
      h("tr", [
        h("th", [
          h("button", {
            class: "btn plan-band",
            type: "button",
            disabled: ui.disabled,
            title: `整段选中 ${band.name}（替换当前选择）`,
            onClick: () => ui.setSelection(nodeId, band.mhz.map((_, j) => `${band.letter}${j + 1}`)),
          }, [h("b", band.letter), ` ${band.name}`]),
        ]),
        ...band.mhz.map((mhz, j) => {
          const name = `${band.letter}${j + 1}`;
          const on = selection.includes(name);
          return h("td", [
            h("button", {
              class: ["btn", "plan-chan", on ? "on" : null],
              type: "button",
              disabled: ui.disabled,
              title: `${name}: ${mhz} MHz`,
              onClick: () => ui.toggleChannel(nodeId, name),
            }, [h("b", name), h("span", { class: "plan-mhz" }, String(mhz))]),
          ]);
        }),
      ])
    )),
  ]);

  const unknown = plan.unknown.length
    ? h("p", { class: "note note--warn" }, `板子上还有这张表不认识的频道：${plan.unknown.join("、")}（缩放档会报自己的名字）。它们没被改动，也没画进格子`)
    : null;

  const enable = cached.profile && Array.isArray(cached.profile.channels) && cached.profile.channels.length
    ? h("div", { class: "ctl" }, [
        h("label", { class: "ctl-label", title: "ENABLE <掩码>：每一位对应 PROFILE? 里的一个档位号" }, "扫哪几档（ENABLE）"),
        h("div", { class: "ctl-row plan-enable" }, cached.profile.channels.map((channel) => {
          const bit = 1 << channel.id;
          const on = (st.enabled & bit) !== 0;
          return h("label", { class: "plan-check", title: `${channel.name} · ${channel.khz} kHz · 档位 ${channel.id}` }, [
            h("input", {
              type: "checkbox",
              checked: on,
              disabled: ui.disabled,
              onChange: (event) => {
                const mask = event.target.checked ? st.enabled | bit : st.enabled & ~bit;
                ui.apply(nodeId, `ENABLE ${mask}`);
              },
            }),
            `${channel.name}`,
          ]);
        })),
      ])
    : h("p", { class: "note" }, "还没读到 PROFILE?：启用位要按板子自己的档位号来算，读回来才有");

  return h("div", { class: "ctl-group" }, [
    h("h3", { class: "ctl-group-title" }, "频道计划（CHANNELS）"),
    h("p", { class: "note" }, cached.profile
      ? `板子现在报的是 ${plan.names.length ? plan.names.join("、") : "（空）"}；下面是编辑器，改完点「下发计划」才生效`
      : "还没读到板子的计划：点「重读板子」取回来，或直接勾一组新的"),
    // 六段 × 八档比最窄的格子宽：横滚放在这一层，不让整格跟着左右晃。
    h("div", { class: "plan-scroll" }, [grid]),
    unknown,
    h("p", { class: "ctl-actions" }, [
      h("button", {
        class: "btn btn--primary",
        type: "button",
        disabled: ui.disabled || !selection.length,
        title: selection.length ? channelsCommand(normalizePlan(selection)) : "先勾至少一档",
        onClick: async () => {
          const answer = await ui.apply(nodeId, channelsCommand(normalizePlan(selection)), { profile: true });
          // 下发成功就把草稿丢掉：格子此后显示的是**板子接受的那一份**，不是我想发的那一份。
          if (answer && answer.ok) {
            const next = { ...ui.planDraft.value };
            delete next[nodeId];
            ui.planDraft.value = next;
          }
        },
      }, "下发计划"),
      h("button", {
        class: "btn",
        type: "button",
        disabled: ui.disabled,
        onClick: () => ui.setSelection(nodeId, []),
      }, "清空"),
      h("span", { class: ui.planNote.value ? "warn" : "muted" }, ui.planNote.value || `${selection.length} / ${MAX_CHANNELS} 档${full ? "（已满）" : ""}`),
    ]),
    enable,
  ]);
}

// --- 逐条控件 -------------------------------------------------------------------

function modeRow(ui) {
  return row(ui, "LO 策略（MODE）", "GROUP：一组频道共用一个 LO；SINGLE：每档一个 LO",
    seg(ui, [["GROUP", "组 LO"], ["SINGLE", "每档一个 LO"]], ui.st.mode, (value) => ui.apply(ui.nodeId, `MODE ${value}`)));
}

function gainRow(ui) {
  const { h } = ui.rt;
  const { nodeId, st } = ui;
  const max = st.gain_max || 82;
  const draft = ui.gainDraft.value[nodeId];
  const shown = draft === undefined ? st.gain : draft;
  const manual = st.gain_mode === "MANUAL";
  const commit = (value) => {
    ui.gainDraft.value = { ...ui.gainDraft.value, [nodeId]: value };
  };
  return row(ui, "接收增益（GAIN，PHY 表索引，不是 dB）", "GAIN MANUAL <索引> / GAIN HARDWARE（AGC）",
    seg(ui, [["MANUAL", "手动"], ["HARDWARE", "AGC"]], st.gain_mode, (value) =>
      ui.apply(nodeId, value === "MANUAL" ? `GAIN MANUAL ${shown}` : "GAIN HARDWARE")),
    h("input", {
      class: "ctl-range",
      type: "range",
      min: "0",
      max: String(max),
      step: "1",
      value: String(shown),
      disabled: ui.disabled || !manual,
      title: `0…${max}（板子报的 gain_max）`,
      onInput: (event) => commit(Number(event.target.value)),
      onChange: async (event) => {
        const value = Number(event.target.value);
        commit(value);
        const answer = await ui.apply(nodeId, `GAIN MANUAL ${value}`);
        if (answer && answer.ok) {
          const next = { ...ui.gainDraft.value };
          delete next[nodeId];
          ui.gainDraft.value = next;
        }
      },
    }),
    h("span", { class: "num" }, `${shown} / ${max}`)
  );
}

function filterRow(ui) {
  const { h } = ui.rt;
  return row(ui, "模拟滤波码（FILTER）", "-1 = PHY 自动；60 ≈ 13 MHz，0 ≈ 69 MHz",
    number(ui, {
      value: ui.st.filter,
      min: -1,
      max: 63,
      step: 1,
      width: 64,
      onChange: (value) => ui.apply(ui.nodeId, value < 0 ? "FILTER AUTO" : `FILTER ${value}`),
    }),
    h("span", { class: "muted" }, "-1 = 自动")
  );
}

function fftRow(ui) {
  const sizes = FFT_SIZES.includes(ui.st.nfft) ? FFT_SIZES : [...FFT_SIZES, ui.st.nfft];
  return row(ui, "FFT 点数（FFT）", "板子按 4 段采样平均（Welch）：点数大分辨率细，点数小刷新快",
    select(ui, {
      value: ui.st.nfft,
      options: sizes.map((n) => [n, `${n} 点（${4 * n} 样本）`]),
      onChange: (value) => ui.apply(ui.nodeId, `FFT ${value}`),
    }));
}

function metricsRow(ui) {
  return row(ui, "每档测什么（METRICS）", "RSSI/SNR 只算计圈要用的量，更快；其余留空",
    seg(ui, [["FULL", "全量"], ["RSSI", "RSSI"], ["SNR", "SNR"]], ui.st.metrics ?? "FULL", (value) =>
      ui.apply(ui.nodeId, `METRICS ${value}`)));
}

function winRow(ui) {
  const { st, nodeId } = ui;
  const send = (rssi, search) => ui.apply(nodeId, `WIN ${Math.round(rssi * 1000)} ${Math.round(search * 1000)}`);
  return row(ui, "RSSI ± kHz · 搜索 ± kHz（WIN）", "页面按 kHz 填，发出去的是 Hz（固件单位）",
    number(ui, { value: st.rssi_hz / 1000, min: 10, max: 20000, step: 10, onChange: (value) => send(value, st.search_hz / 1000) }),
    number(ui, { value: st.search_hz / 1000, min: 10, max: 20000, step: 10, onChange: (value) => send(st.rssi_hz / 1000, value) })
  );
}

function emaRow(ui) {
  const { st, nodeId } = ui;
  const send = (ema, clear) => ui.apply(nodeId, `EMA ${Math.round(ema)} ${Math.round(clear)}`);
  return row(ui, "平滑 RSSI · 通道底噪（EMA，ms）", "时间常数：任何刷新率下都是同一个平滑力度",
    number(ui, { value: st.ema_ms, min: 1, max: 60000, step: 10, onChange: (value) => send(value, st.clear_ms) }),
    number(ui, { value: st.clear_ms, min: 1, max: 60000, step: 100, onChange: (value) => send(st.ema_ms, value) })
  );
}

function specRow(ui) {
  const { h } = ui.rt;
  const { st, nodeId } = ui;
  const send = (every, bins, detector) => ui.apply(nodeId, `SPEC ${every} ${bins} ${st.spec_span_hz} ${detector}`);
  return row(ui, "显示频谱：每 N 跳一次 · bins · 检波（SPEC）", "0 = 关；跨度沿用板子当前的 spec_span_hz，这一格不改它",
    number(ui, { value: st.spec_every, min: 0, max: 255, step: 1, width: 56, onChange: (value) => send(value, st.spec_bins, st.spec_detector ? "MAX" : "MEAN") }),
    select(ui, {
      value: st.spec_bins,
      options: SPEC_BINS.map((n) => [n, `${n} bins`]),
      onChange: (value) => send(st.spec_every, Number(value), st.spec_detector ? "MAX" : "MEAN"),
    }),
    select(ui, {
      value: st.spec_detector ? "MAX" : "MEAN",
      options: [["MAX", "max"], ["MEAN", "mean"]],
      onChange: (value) => send(st.spec_every, st.spec_bins, value),
    }),
    h("span", { class: "muted" }, `跨度 ${(st.spec_span_hz / 1e6).toFixed(1)} MHz`)
  );
}

function recalRow(ui) {
  return row(ui, "接收校准（RECALMODE）", "CACHE：按 LO 缓存；ONCE：只校一次；EVERY：每次调谐都校",
    seg(ui, [["CACHE", "缓存"], ["ONCE", "一次"], ["EVERY", "每次"]], ui.st.recal, (value) =>
      ui.apply(ui.nodeId, `RECALMODE ${value}`)),
    ui.rt.h("button", {
      class: "btn",
      type: "button",
      disabled: ui.disabled,
      title: "RECAL：现在就校一次",
      onClick: () => ui.apply(ui.nodeId, "RECAL"),
    }, "立即校准")
  );
}

function settleRow(ui) {
  return row(ui, "调谐后稳定时间（SETTLE，µs）", "换 LO 之后等多久再采样",
    number(ui, { value: ui.st.settle_us, min: 0, max: 20000, step: 50, onChange: (value) => ui.apply(ui.nodeId, `SETTLE ${value}`) }));
}

function tskipRow(ui) {
  const mask = ui.st.tskip;
  return row(ui, "整调之间跳过什么（TUNESKIP，调试）", "位掩码：1 只调 PLL、2 跳过 RX 通路、4 跳过增益。C5 上跳过增益是错的",
    ui.rt.h("span", { class: "seg" }, TSKIP_BITS.map(([bit, label]) =>
      ui.rt.h("button", {
        class: ["btn", mask !== undefined && (mask & bit) !== 0 ? "on" : null],
        type: "button",
        disabled: ui.disabled || mask === undefined,
        title: mask === undefined ? "板子没报 tskip" : `TUNESKIP ${mask ^ bit}`,
        onClick: () => ui.apply(ui.nodeId, `TUNESKIP ${mask ^ bit}`),
      }, label)
    ))
  );
}

function iqRow(ui) {
  return row(ui, "IQ 校正（IQCORR）", "板端的直流/镜像校正",
    seg(ui, [["1", "开"], ["0", "关"]], String(ui.st.iq_corr), (value) => ui.apply(ui.nodeId, `IQCORR ${value}`)));
}

function deviceRow(ui) {
  const { h } = ui.rt;
  return row(ui, "设备", "这两条都不可逆：重启会断流，烧录模式要重新烧固件或复位才回得来",
    h("button", {
      class: "btn",
      type: "button",
      disabled: ui.disabled,
      title: "REBOOT",
      onClick: () => {
        if (ui.ask(`重启节点 ${ui.nodeId}？扫描会中断，直到它重新起来。`)) ui.apply(ui.nodeId, "REBOOT");
      },
    }, "重启"),
    h("button", {
      class: "btn btn--danger",
      type: "button",
      disabled: ui.disabled,
      title: "BOOTLOADER：进 ROM 下载模式",
      onClick: () => {
        if (ui.ask(`让节点 ${ui.nodeId} 进 ROM 烧录模式？扫描会停，直到重新烧固件或复位。`)) ui.apply(ui.nodeId, "BOOTLOADER");
      },
    }, "进烧录模式")
  );
}