/**
 * 原始 I/Q 快照：原单机控制台那张卡（`web/src/views/rawiq.ts`）搬到站点侧，
 * **一格只对一块板抓**，抓到的三份图（星座 / 时间波形 / 客户端 FFT 谱）都在这一格里。
 *
 * 算法不在这里：`iq.js` 是纯计算（与单机侧逐行同源），`chart.js` 提供刻度与后备缓冲。
 * 这一格只负责"抓哪块板、抓哪个 LO、抓多少点"和把结果画出来。
 *
 * 多点接入带来的四点不同：
 *
 * - **对象常驻可见**（`controlHead`），LO 与样本数的草稿**按板分开**：切板不该把上一块板的
 *   LO 带过来——那会让运营者以为在 B 板上抓的是同一个频点。
 * - **样本数上限由板子说**（`STATUS?` 的 `rawiq_max`），超上限的选项置灰而不是发出去等
 *   `ERR rawiq_args`。读不到 STATUS? 时不假装知道上限。
 * - **"用当前档的 LO"有出处**：优先取 `STATUS?` 的 `plan` 里"联动那一档所在跳"的 LO（那是
 *   板子真在用的调谐值，一定是步长的整数倍），其次 `PROFILE?` 的档位中心频率，再次帧 LO。
 *   每一步都把出处印出来，不让人以为填进去的数就是那一档的中心。
 * - **不轮询**（R21）：抓一次就是一次占用，抓完不自动续抓。
 *
 * 单机侧的 RotorHazard 导频名没有搬：这一页没有 RH 服务端那一路（C-7 裁定留在单机侧），
 * 编一个导频名出来就是把"没有的事实"画成有。
 */

import { colorsFrom, frame, niceStep, resizeCanvas } from "../chart.js";
import { spectrum, summarize } from "../iq.js";
import { controlBlocked, controlHead, createControl } from "./control.js";
import { empty, fmtKhzAsMhz } from "./parts.js";
import { controlTarget, linkedSlot } from "./select.js";

export const plugin_id = "node-rawiq";

/** 样本数选项与单机侧同一套；默认 8192（够 8 段 1024 点 FFT，又不至于把串口占太久）。 */
const SIZES = [1024, 4096, 8192, 16380];
const DEFAULT_SIZE = 8192;
/** 时间波形只画前这么多个点：再多就是在 480 px 上画几千条竖线，看不出形状还卡。 */
const TRACE_POINTS = 400;
const NFFT = 1024;

export function create(rt, deps) {
  const { h, ref } = rt;

  return {
    name: "NodeRawIq",
    props: { widget: { type: Object, required: true } },
    setup(props) {
      const widget = props.widget;
      const ctl = createControl(rt, deps, widget);
      const loDraft = ref({});
      const sizeDraft = ref({});
      const loFrom = ref({});
      const constel = ref(null);
      const trace = ref(null);
      const spec = ref(null);
      /** 算过的谱按快照对象记住：一次 FFT 是 8 段 × 1024 点，不该每次重渲染都重算。 */
      let memo = { snapshot: null, facts: null, spec: null };
      let painted = null;
      let seenNode = null;

      const writable = () => deps.context.state.instanceRole !== "aggregator";
      const locked = () => !writable() || ctl.busy.value;

      function sync() {
        const target = controlTarget(deps, widget);
        const id = target.board ? target.board.node_id : null;
        if (id === seenNode) return;
        seenNode = id;
        if (id !== null) ctl.read(id, { profile: true });
      }

      function factsOf(snapshot) {
        if (memo.snapshot !== snapshot) {
          memo = { snapshot, facts: summarize(snapshot), spec: spectrum(snapshot, { nfft: NFFT }) };
        }
        return memo;
      }

      function paint(snapshot) {
        // 谱那一格在样本不够时根本不渲染（换成空状态），所以它缺席也要照样画前两张。
        if (!snapshot || !constel.value || !trace.value) return;
        const { facts, spec: band } = factsOf(snapshot);
        const colors = palette(constel.value);
        const boxes = [constel.value, trace.value, spec.value];
        // 签名里带尺寸：格子被拖动改大小之后必须重画，否则图还是旧像素尺寸（糊或裁）。
        const signature = `${boxes.map((box) => (box ? `${box.clientWidth}x${box.clientHeight}` : "-")).join("|")}#${colors.ink}`;
        if (painted && painted.signature === signature && painted.snapshot === snapshot) return;
        drawConstellation(boxes[0], snapshot, facts, colors);
        drawTrace(boxes[1], snapshot, facts, colors);
        if (boxes[2]) drawSpectrum(boxes[2], band, colors);
        painted = { signature, snapshot };
      }

      rt.onMounted(sync);
      rt.onUpdated(sync);

      return () => {
        deps.signal.tick.value;
        ctl.busy.value;
        ctl.failure.value;
        ctl.boards.value;
        loDraft.value;
        sizeDraft.value;
        loFrom.value;

        const target = controlTarget(deps, widget);
        if (!target.board) return h("div", { class: "node-control" }, [controlBlocked(rt, target)]);

        const board = target.board;
        const nodeId = board.node_id;
        const cached = ctl.boardOf(nodeId);
        const st = cached.status;
        const snapshot = cached.rawiq;
        const slot = linkedSlot(deps);
        const disabled = locked();
        const max = st && Number.isFinite(st.rawiq_max) ? st.rawiq_max : null;
        const size = pickSize(sizeDraft.value[nodeId], max);
        const lo = loDraft.value[nodeId];
        const pick = loPick(deps, cached, nodeId, slot);
        const ready = Number.isFinite(lo) && lo > 0 && size !== null;

        const head = controlHead(rt, deps, {
          board,
          target,
          ctl,
          note: "抓一次就是一次串口占用：抓完不自动续抓，也不轮询。RAW IQ 帧只走 USB 口，UART 接入的板子会回 ERR uart_port。",
          actions: [
            h("button", {
              class: "btn",
              type: "button",
              disabled,
              title: "STATUS? + PROFILE?：取回 rawiq_max 与频道计划",
              onClick: () => ctl.read(nodeId, { profile: true }),
            }, "重读板子"),
          ],
        });

        const capNote = capabilityNote(rt, board);
        const controls = h("div", { class: "ctl-group" }, [
          h("h3", { class: "ctl-group-title" }, "抓一份快照（RAWIQ）"),
          h("div", { class: "ctl" }, [
            h("label", { class: "ctl-label", title: "RAWIQ <lo_khz> <samples>：LO 单位 kHz，要是板子调谐步长的整数倍" }, "LO（kHz）"),
            h("div", { class: "ctl-row" }, [
              h("input", {
                class: "ctl-num ctl-num--wide",
                type: "number",
                min: "1",
                step: "1",
                value: lo === undefined ? "" : String(lo),
                disabled,
                title: loFrom.value[nodeId] || "填 kHz，或点右边那颗钮从板子读回来的事实里取",
                onInput: (event) => {
                  const raw = event.target.value;
                  loDraft.value = { ...loDraft.value, [nodeId]: raw === "" ? undefined : Number(raw) };
                  loFrom.value = { ...loFrom.value, [nodeId]: "手填" };
                },
              }),
              h("button", {
                class: "btn",
                type: "button",
                disabled: disabled || !pick,
                title: pick ? `取 ${pick.khz} kHz：${pick.from}` : "还没有可取的 LO：先重读板子，或在矩阵里点一档联动",
                onClick: () => {
                  loDraft.value = { ...loDraft.value, [nodeId]: pick.khz };
                  loFrom.value = { ...loFrom.value, [nodeId]: pick.from };
                },
              }, slot === null ? "用板子报的 LO" : `用第 ${slot + 1} 档的 LO`),
              h("select", {
                class: "ctl-select",
                value: size === null ? "" : String(size),
                disabled: disabled || size === null,
                title: max === null ? "还没读到 STATUS? 的 rawiq_max：上限未知" : `板子报的 rawiq_max = ${max}`,
                onChange: (event) => { sizeDraft.value = { ...sizeDraft.value, [nodeId]: Number(event.target.value) }; },
              }, SIZES.map((n) => h("option", {
                value: String(n),
                selected: n === size,
                disabled: max !== null && n > max,
              }, `${n} 样本`))),
              h("button", {
                class: "btn btn--primary",
                type: "button",
                disabled: disabled || !ready,
                title: ready ? `RAWIQ ${lo} ${size}` : "先填 LO（或点左边那颗钮），并选一个板子允许的样本数",
                onClick: () => ctl.send(nodeId, `RAWIQ ${lo} ${size}`),
              }, ctl.busy.value ? "抓取中……" : "抓一份"),
            ]),
          ]),
          h("p", { class: "note" }, [
            loFrom.value[nodeId] ? `LO 出处：${loFrom.value[nodeId]}。` : "LO 还没有出处：点上面那颗钮，或自己填。",
            max === null ? " 样本上限未知（还没读到 STATUS?）。" : ` 样本上限 ${max}（板子报的 rawiq_max）。`,
            size === null ? " 板子报的上限比最小选项还小：这一格发不出去，改用协议控制台自己写 RAWIQ。" : "",
          ]),
          capNote,
        ]);

        if (!snapshot) {
          return h("div", { class: "node-control node-rawiq" }, [
            head,
            controls,
            empty(
              rt,
              "这块板还没有快照",
              ctl.busy.value ? "正在抓……" : "填好 LO 与样本数点「抓一份」；抓不到时板子的原话会印在上面那行"
            ),
          ]);
        }

        const { facts, spec: band } = factsOf(snapshot);
        rt.nextTick(() => paint(snapshot));

        return h("div", { class: "node-control node-rawiq" }, [
          head,
          controls,
          h("p", { class: "meta iq-info" }, infoText(facts)),
          h("div", { class: "iq-grid" }, [
            h("canvas", { ref: constel, class: "chart iq-chart", "aria-label": `I/Q 星座（节点 ${nodeId}）` }),
            h("canvas", { ref: trace, class: "chart iq-chart", "aria-label": `I/Q 时间波形（节点 ${nodeId}）` }),
          ]),
          band.segs
            ? h("canvas", { ref: spec, class: "chart iq-chart iq-chart--wide", "aria-label": `客户端 FFT 谱（节点 ${nodeId}）` })
            : empty(rt, `快照只有 ${facts.n} 点，不够一段 ${NFFT} 点 FFT`, "样本数选大一些再抓；不画一条假底噪"),
          h("p", { class: "note" }, band.segs
            ? `谱是 ${band.segs} 段 ${NFFT} 点 Hann 窗 FFT 的平均，纵轴 dBFS/bin（满量程 10-bit 复正弦读 0 dB），横轴是这次抓取的真实频率跨度——它不是扫描出来的连续频谱。`
            : "星座与波形用的是传输计数：乘上 scale 才是 10-bit ADC 计数，上面那行已经换算过。"),
        ]);
      };
    },
  };
}

// --- 取数与文案 -----------------------------------------------------------------

/** 样本数：草稿优先，但**板子说不行的选项一律不选**。上限未知时不假装知道。 */
function pickSize(draft, max) {
  const allowed = SIZES.filter((n) => max === null || n <= max);
  if (!allowed.length) return null;
  if (draft !== undefined && allowed.includes(draft)) return draft;
  return allowed.includes(DEFAULT_SIZE) ? DEFAULT_SIZE : allowed[allowed.length - 1];
}

/**
 * "当前档的 LO"到底取哪个数，按可靠性排序，并把出处一起带出去：
 * `plan` 里那一跳的 LO 是板子真在调的值（一定合法）；`PROFILE?` 的中心频率是这一档的名义
 * 频率（C5 这类整 MHz 调谐的板子可能不收）；帧 LO 是这一帧共用的那个，与"这一档"不是一回事。
 */
function loPick(deps, cached, nodeId, slot) {
  const plan = cached.status && Array.isArray(cached.status.plan) ? cached.status.plan : [];
  if (slot !== null) {
    const hop = plan.find((row) => Array.isArray(row.ch) && row.ch.includes(slot));
    if (hop && Number.isFinite(hop.lo_khz)) {
      return { khz: hop.lo_khz, from: `第 ${slot + 1} 档所在那一跳的 LO（STATUS? 的 plan，板子真在用的调谐值）` };
    }
    const channel = cached.profile && (cached.profile.channels || []).find((row) => row.id === slot);
    if (channel && Number.isFinite(channel.khz)) {
      return { khz: channel.khz, from: `第 ${slot + 1} 档的中心频率（PROFILE?，不是调谐值：板子可能按步长拒绝）` };
    }
  }
  const row = deps.live.node(nodeId);
  if (row && row.loKhz !== null && row.loKhz !== undefined) {
    return { khz: row.loKhz, from: "这一帧共用的 LO（/ws/live，不是某一档的中心频率）" };
  }
  if (plan.length && Number.isFinite(plan[0].lo_khz)) {
    return { khz: plan[0].lo_khz, from: "计划里第一跳的 LO（STATUS? 的 plan）" };
  }
  return null;
}

/** 板端明确声明不支持时说一声，但**不挡下发**：命令的权威是板子，它自己的回答会印出来。 */
function capabilityNote(rt, board) {
  const declared = (board.capabilities || {}).rawiq;
  if (!declared || declared.supported !== false) return null;
  return rt.h("p", { class: "note note--warn" }, `板端声明这块板不支持 RAW IQ（${declared.reason || "未说明理由"}）：仍可试发，板子的回答说了算。`);
}

/** 快照的标量事实一行。ADC 计数一律乘 `lsb`——不乘就是把量程说小了 2^shift 倍。 */
function infoText(facts) {
  const counts = (value) => (value * facts.lsb).toFixed(1);
  return [
    `LO ${fmtKhzAsMhz(facts.loKhz)}`,
    `${(facts.rateHz / 1e6).toFixed(3)} MS/s`,
    `${facts.n} 样本`,
    `scale ×${facts.lsb}（10-bit 计数）`,
    `直流 (${counts(facts.dcI)}, ${counts(facts.dcQ)})`,
    `RMS ${counts(facts.rms)}`,
    `峰值 ${Math.round(facts.peak * facts.lsb)}/512`,
    facts.conjugated ? "已共轭" : null,
  ].filter(Boolean).join(" · ");
}

// --- 绘制 -----------------------------------------------------------------------

function palette(canvas) {
  const read = (name) => getComputedStyle(canvas).getPropertyValue(name);
  const base = colorsFrom(read);
  const pick = (name, alt) => {
    const value = read(name);
    return value && value.trim() ? value.trim() : alt;
  };
  return { ...base, ink: pick("--ink", "#ffffff"), accent: pick("--accent", base.series) };
}

/** 等距刻度。步长用 `chart.js` 的 `niceStep`：同一套刻度算法，两张页面的轴不会长得不一样。 */
function ticks(lo, hi, count) {
  const step = niceStep(hi - lo, count);
  const out = [];
  for (let value = Math.ceil(lo / step) * step; value <= hi + 1e-9; value += step) {
    out.push(Number(value.toFixed(6)));
  }
  return out;
}

function gridLines(ctx, axis, xs, ys, colors, fmtX, fmtY) {
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  ctx.font = colors.font;
  ctx.fillStyle = colors.muted;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const value of ys) {
    const y = axis.sy(value);
    ctx.beginPath();
    ctx.moveTo(axis.left, y);
    ctx.lineTo(axis.right, y);
    ctx.stroke();
    ctx.fillText(fmtY(value), axis.left - 6, y);
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const value of xs) {
    const x = axis.sx(value);
    ctx.beginPath();
    ctx.moveTo(x, axis.top);
    ctx.lineTo(x, axis.bottom);
    ctx.stroke();
    ctx.fillText(fmtX(value), x, axis.bottom + 6);
  }
}

function caption(ctx, colors, text) {
  ctx.fillStyle = colors.muted;
  ctx.font = colors.font;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(text, 3, 2);
}

/** 星座：低透明度点堆出密度。量程按数据定（`facts.range`），满量程会把噪声缩成中心一个点。 */
function drawConstellation(canvas, snapshot, facts, colors) {
  const { ctx, w, h } = resizeCanvas(canvas);
  const size = Math.max(1, Math.min(w, h));
  const axis = frame({ w: size, h: size }, { x: [-facts.range, facts.range], y: [-facts.range, facts.range] }, { left: 0, right: 0, top: 0, bottom: 0 });
  ctx.save();
  ctx.translate((w - size) / 2, 0);
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, size - 1, size - 1);
  ctx.beginPath();
  ctx.moveTo(size / 2, 0);
  ctx.lineTo(size / 2, size);
  ctx.moveTo(0, size / 2);
  ctx.lineTo(size, size / 2);
  ctx.stroke();
  ctx.fillStyle = colors.accent;
  ctx.globalAlpha = 0.25;
  for (let j = 0; j < snapshot.n; j++) {
    ctx.fillRect(axis.sx(snapshot.i[j]) - 1, axis.sy(snapshot.q[j]) - 1, 2, 2);
  }
  ctx.globalAlpha = 1;
  caption(ctx, colors, `I/Q 星座 · ±${facts.range} 传输计数`);
  ctx.restore();
}

/** 时间波形：前 400 点的 I 与 Q，横轴 µs。两条线用分类色板的前两色，与别的图同一套。 */
function drawTrace(canvas, snapshot, facts, colors) {
  const { ctx, w, h } = resizeCanvas(canvas);
  const n = Math.min(TRACE_POINTS, snapshot.n);
  const us = (n / snapshot.rateHz) * 1e6;
  const axis = frame({ w, h }, { x: [0, us], y: [-facts.range, facts.range] }, { left: 34, right: 10, top: 16, bottom: 22 });
  gridLines(ctx, axis, ticks(0, us, 4), ticks(-facts.range, facts.range, 4), colors, (v) => `${v.toFixed(1)}µs`, (v) => String(v));
  const palette = colors.seriesList.length ? colors.seriesList : [colors.series, colors.accent];
  [snapshot.i, snapshot.q].forEach((arr, index) => {
    ctx.strokeStyle = palette[index % palette.length];
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (let j = 0; j < n; j++) {
      const x = axis.sx((j / snapshot.rateHz) * 1e6);
      const y = axis.sy(arr[j]);
      if (j) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.stroke();
  });
  caption(ctx, colors, `I（${palette[0]}）· Q（${palette[1 % palette.length]}）· 前 ${n} 点，传输计数`);
}

/** 谱：`iq.spectrum` 算好的 dBFS/bin，横轴绝对频率（LO ± 采样率/2），标签按 MHz 印。 */
function drawSpectrum(canvas, band, colors) {
  const { ctx, w, h } = resizeCanvas(canvas);
  if (!band.segs) return;
  const axis = frame({ w, h }, { x: [band.f0, band.f1], y: [band.lo, band.hi] }, { left: 46, right: 10, top: 16, bottom: 22 });
  gridLines(ctx, axis, ticks(band.f0, band.f1, 6), ticks(band.lo, band.hi, 4), colors, (v) => (v / 1000).toFixed(1), (v) => String(v));
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  band.dbs.forEach((value, k) => {
    const x = axis.sx(band.f0 + ((k + 0.5) / band.nfft) * (band.f1 - band.f0));
    const y = axis.sy(value);
    if (k) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.stroke();
  caption(ctx, colors, `dBFS/bin · 抓取跨度 ${(band.rateHz / 1e6).toFixed(3)} MHz · 横轴 MHz`);
}