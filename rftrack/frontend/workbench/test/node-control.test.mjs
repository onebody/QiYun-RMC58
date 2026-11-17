/**
 * 写侧三格的渲染与交互：**扫描控制 / 协议控制台 / 原始 I/Q**。
 *
 * 不开浏览器（无构建形态下 `rt` 是参数，假 `h` 就能断言树形与文案），但**命令真的发**：
 * 假板在 `support/control-fixture.mjs`，`api` 是真的 `createApi`。于是"点这颗钮到底发了
 * 哪一行"是逐字断言出来的，不是看着像对。
 *
 * 钉的是这几条多点接入才有的判据：
 *
 * - 对象常驻可见，勾多块就拒绝下发（不广播、不挑第一块）；
 * - 读数与草稿按板分开，切板不带上一块板的数字；
 * - 不轮询：挂载/切板/下发后/点重读之外不发命令，租约里也不许有定时器；
 * - 板子的 `ERR` 原词一个不改，注解只是加注；
 * - 不可逆的两条（重启/烧录）在没有确认时宁可不动。
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import * as nodeControls from "../components/node-controls.js";
import * as nodeConsole from "../components/node-console.js";
import * as nodeRawIq from "../components/node-rawiq.js";
import { point, samplesFrame } from "./support/live-fixture.mjs";
import { R1_REPLIES, STATUS_FRESH, controlEnv, fakeBoard, toneSnapshot } from "./support/control-fixture.mjs";
import { clickNode, fakeCanvas, findByClass, hasClass, textOf, walk } from "./support/vue-fixture.mjs";

// --- 小件 -----------------------------------------------------------------------

const buttons = (node) => walk(node).filter((row) => row.tag === "button");
const byTitle = (node, title) => walk(node).find((row) => row.props.title === title);
const press = (node, text) => clickNode(node, (row) => row.tag === "button" && textOf(row) === text);
const rerender = async (env, tree) => { await env.flush(); return tree(); };

/** 按那一行左边的标签找控件行：控件是按它写的那句话认的，不是按第几个。 */
function ctlRow(node, label) {
  const found = findByClass(node, "ctl").find((row) => {
    const head = findByClass(row, "ctl-label")[0];
    return head && textOf(head) === label;
  });
  assert.ok(found, `没有这一行控件：${label}`);
  return found;
}

const fields = (row) => walk(row).filter((node) => node.tag === "input" || node.tag === "select");

/**
 * 只数"折线"上的 lineTo：一条 beginPath→moveTo 后连着 ≥2 个 lineTo 才算数据折线。
 * 网格线每条是 moveTo→单个 lineTo→stroke（run=1），星座的十字准星也是单段，都不计。
 * 这样断言的是"波形两条各 400 点、谱一条 1024 点"，与网格刻度数无关。
 */
function polylineLineTos(box) {
  let count = 0;
  let run = 0;
  const flush = () => { if (run > 1) count += run; run = 0; };
  for (const op of box.ops) {
    if (op[0] === "lineTo") run += 1;
    else if (op[0] === "moveTo" || op[0] === "beginPath" || op[0] === "stroke") flush();
  }
  flush();
  return count;
}

/** 四样本的手算快照：直流 (1,2)、RMS √8、峰值 4，shift=2 → ADC 计数要乘 4。 */
const TINY = { loKhz: 5658000, rateHz: 12_000_000, n: 4, shift: 2, conjugated: false, i: [3, -1, 3, -1], q: [4, 0, 4, 0] };

// === 扫描控制 ===================================================================

test("扫描控制：对象常驻可见——板名/节点/接入机/传输方式与「会停 RH」都印在头部", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  const head = findByClass(node, "ctl-head")[0];
  assert.ok(head, "头部那一行没了，运营者就不知道自己在写哪块板");
  assert.match(textOf(head), /板 R1/);
  assert.match(textOf(head), /节点 30 · 接入机 桥 A · 传输 uart \/dev\/ttyAMA0/);
  assert.equal(textOf(findByClass(head, "chip--warn")[0]), "会停 RH");
  assert.equal(findByClass(head, "badge--api").length, 1);
  assert.match(textOf(head), /单写者：这一格的命令只发给上面那块板/);
  assert.match(textOf(head), /不轮询/);
  assert.equal(findByClass(head, "ctl-picker").length, 1, "名册里有两块板，就该给一个切板的下拉");
});

test("扫描控制：挂载读一次 STATUS? + PROFILE?，此后再不发命令，租约里也没有定时器", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?"]);
  assert.deepEqual(env.widget.lease.timers, [], "不轮询：读板会占住串口，占着串口 RotorHazard 就起不来");
  await env.flush();
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?"], "等一拍也不许自己再读");
});

test("扫描控制：还没读到 STATUS? 就不画控件，免得把猜出来的值当成板子的设置", async () => {
  const env = controlEnv({ fakes: { 30: fakeBoard({ replies: { "STATUS?": "ERR busy" } }) } });
  const tree = env.mount(nodeControls.create);
  const busy = tree();
  assert.match(textOf(busy), /还没读到这块板的状态/);
  assert.match(textOf(busy), /正在读……/);
  const node = await rerender(env, tree);
  assert.equal(findByClass(node, "ctl-group").length, 0);
  assert.match(textOf(node), /点「重读板子」取回 STATUS\?/);
  assert.match(textOf(node), /ERR busy/, "板子的原话要印出来");
});

test("扫描控制：勾了两块板就拒绝下发，说清是哪两块与怎么改，一条命令也不发", async () => {
  const env = controlEnv({ effective: { nodes: [30, 31] } });
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /控制是单写者，这一格勾了 2 块板（板 R1、板 R2）/);
  assert.match(textOf(node), /控制不广播：一条命令只发给一块板/);
  assert.equal(buttons(node).length, 0);
  assert.deepEqual(env.calls, []);
});

test("扫描控制：在扫的板给「停止扫描」，已停的板给「开始扫描」，发完立刻重读", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  assert.equal(textOf(findByClass(node, "chip--active")[0]), "在扫");
  press(node, "停止扫描");
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent.slice(2), ["SCAN STOP", "STATUS?"]);

  const other = controlEnv({ nodeId: 31 });
  const tree2 = other.mount(nodeControls.create);
  const node2 = await rerender(other, tree2);
  assert.equal(textOf(findByClass(node2, "chip--idle")[0]), "已停");
  press(node2, "开始扫描");
  await rerender(other, tree2);
  assert.deepEqual(other.fakes[31].sent.slice(2), ["SCAN START", "STATUS?"]);
});

test("扫描控制：增益拖动只改草稿，松手才下发；下发成功后显示的是板子报的那一份", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const row = ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）");
  const range = fields(row).find((field) => field.props.type === "range");
  assert.equal(range.props.value, "45");
  assert.equal(range.props.max, "82", "上限是板子报的 gain_max，不是写死的");
  assert.match(textOf(row), /45 \/ 82/);

  range.props.onInput({ target: { value: "50" } });
  node = tree();
  assert.match(textOf(ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）")), /50 \/ 82/);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?"], "拖动过程中不发命令");

  fields(ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）"))
    .find((field) => field.props.type === "range")
    .props.onChange({ target: { value: "50" } });
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent.slice(2), ["GAIN MANUAL 50", "STATUS?"]);
  // 假板重读回来还是 45：草稿已经丢掉，格子显示的是**板子接受的那一份**，不是我想发的那一份。
  assert.match(textOf(ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）")), /45 \/ 82/);
});

test("扫描控制：AGC 与手动是两条不同的命令", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  press(node, "AGC");
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "GAIN HARDWARE");
});

test("扫描控制：FILTER 填 -1 发的是 AUTO，不是「FILTER -1」", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const input = () => fields(ctlRow(tree(), "模拟滤波码（FILTER）"))[0];
  assert.equal(input().props.value, "-1");
  input().props.onChange({ target: { value: "60" } });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "FILTER 60");
  fields(ctlRow(node, "模拟滤波码（FILTER）"))[0].props.onChange({ target: { value: "-1" } });
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-2), "FILTER AUTO");
});

test("扫描控制：WIN 页面按 kHz 填，发出去的是 Hz（固件单位）", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const inputs = fields(ctlRow(node, "RSSI ± kHz · 搜索 ± kHz（WIN）"));
  assert.deepEqual(inputs.map((field) => field.props.value), ["100", "600"]);
  inputs[0].props.onChange({ target: { value: "200" } });
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent.slice(2), ["WIN 200000 600000", "STATUS?"]);
  fields(ctlRow(node, "RSSI ± kHz · 搜索 ± kHz（WIN）"))[1].props.onChange({ target: { value: "800" } });
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-2), "WIN 100000 800000");
});

test("扫描控制：EMA 两个时间常数一起发；FFT 与 METRICS 各发各的词", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  fields(ctlRow(node, "平滑 RSSI · 通道底噪（EMA，ms）"))[0].props.onChange({ target: { value: "80" } });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "EMA 80 2000");
  fields(ctlRow(node, "FFT 点数（FFT）"))[0].props.onChange({ target: { value: "512" } });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-2), "FFT 512");
  press(node, "SNR");
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-2), "METRICS SNR");
});

test("扫描控制：SPEC 沿用板子当前的跨度，检波按板子报的那个词发", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const row = ctlRow(node, "显示频谱：每 N 跳一次 · bins · 检波（SPEC）");
  assert.match(textOf(row), /跨度 20\.0 MHz/);
  fields(row)[1].props.onChange({ target: { value: "512" } });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "SPEC 0 512 20000000 MAX");
  fields(ctlRow(node, "显示频谱：每 N 跳一次 · bins · 检波（SPEC）"))[2].props.onChange({ target: { value: "MEAN" } });
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-2), "SPEC 0 256 20000000 MEAN");
});

test("扫描控制：TUNESKIP 是位掩码，点一下发的是异或后的那个数", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  press(node, "跳过增益");
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "TUNESKIP 4");

  const other = controlEnv({ nodeId: 31 });
  const tree2 = other.mount(nodeControls.create);
  const node2 = await rerender(other, tree2);
  const row = ctlRow(node2, "整调之间跳过什么（TUNESKIP，调试）");
  const on = buttons(row).filter((button) => hasClass(button, "on")).map(textOf);
  assert.deepEqual(on, ["只调 PLL", "跳过 RX 通路"], "板子报 tskip=3，这两位就该亮着");
});

test("扫描控制：整段选中 → 下发计划按频率升序，成功后草稿丢掉、格子回到板子报的那一份", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  assert.match(textOf(node), /板子现在报的是 R1、R2、R3、R4、R5、R6、R7、R8/);
  assert.match(textOf(node), /8 \/ 8 档（已满）/);

  clickNode(node, (row) => row.props.title === "整段选中 Boscam A（替换当前选择）");
  node = tree();
  press(node, "下发计划");
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent.slice(2), [
    "CHANNELS A8=5725 A7=5745 A6=5765 A5=5785 A4=5805 A3=5825 A2=5845 A1=5865",
    "STATUS?",
    "PROFILE?",
  ]);
  assert.ok(hasClass(byTitle(node, "R1: 5658 MHz"), "on"), "下发成功后显示的是板子接受的计划，不是我想发的那一份");
  assert.equal(hasClass(byTitle(node, "A1: 5865 MHz"), "on"), false);
});

test("扫描控制：勾满 8 档再勾就提示，提示用的是租约定时器（会自己收掉）", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  clickNode(node, (row) => row.props.title === "A1: 5865 MHz");
  node = tree();
  assert.match(textOf(node), /计划已满（最多 8 档）：先去掉一档/);
  assert.equal(env.widget.lease.timers.length, 1);
  assert.equal(env.widget.lease.timers[0].ms, 1800);
  env.widget.lease.timers[0].fn();
  node = tree();
  assert.match(textOf(node), /8 \/ 8 档（已满）/);
  assert.equal(textOf(node).includes("计划已满"), false);
});

test("扫描控制：ENABLE 的掩码按板子自己的档位号算，不是按格子的列号", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const label = byTitle(node, "R4 · 5769000 kHz · 档位 3");
  assert.ok(label, "启用位那一格要按 PROFILE? 的档位号认");
  const box = walk(label).find((row) => row.tag === "input");
  assert.equal(box.props.checked, true, "板子报 enabled=255，八档全开");
  box.props.onChange({ target: { checked: false } });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent[2], "ENABLE 247", "255 去掉第 3 位 = 247");

  const other = controlEnv({ nodeId: 31 });
  const tree2 = other.mount(nodeControls.create);
  const node2 = await rerender(other, tree2);
  const scaled = byTitle(node2, "X1 · 2412000 kHz · 档位 0");
  walk(scaled).find((row) => row.tag === "input").props.onChange({ target: { checked: false } });
  await rerender(other, tree2);
  assert.equal(other.fakes[31].sent[2], "ENABLE 0", "缩放档只有一档，关掉它就是 0");

  // 反过来：从"第 3 位关着"的板子打开它，掩码要把那位 OR 回去。只测"关"会漏掉 |bit 这一支。
  const status247 = `STATUS ${JSON.stringify({ ...JSON.parse(R1_REPLIES["STATUS?"].slice("STATUS ".length)), enabled: 247 })}`;
  const on = controlEnv({ fakes: { 30: fakeBoard({ replies: { ...R1_REPLIES, "STATUS?": status247 } }) } });
  const treeOn = on.mount(nodeControls.create);
  const nodeOn = await rerender(on, treeOn);
  const offBox = walk(byTitle(nodeOn, "R4 · 5769000 kHz · 档位 3")).find((row) => row.tag === "input");
  assert.equal(offBox.props.checked, false, "enabled=247，第 3 位关着");
  offBox.props.onChange({ target: { checked: true } });
  await rerender(on, treeOn);
  assert.equal(on.fakes[30].sent[2], "ENABLE 255", "247 打开第 3 位 = 255");
});

test("扫描控制：板子上那张表不认识的频道要说出来，不静默丢掉", async () => {
  const env = controlEnv({ nodeId: 31 });
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /板子上还有这张表不认识的频道：X1/);
  assert.match(textOf(node), /它们没被改动，也没画进格子/);
});

test("扫描控制：重启与烧录在没有确认时宁可不动（不可逆的两条）", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  const before = env.fakes[30].sent.length;
  press(node, "重启");
  press(node, "进烧录模式");
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.length, before, "没有 confirm 的环境一律当成没确认");

  const realConfirm = globalThis.confirm;
  globalThis.confirm = () => true;
  try {
    press(node, "重启");
    node = await rerender(env, tree);
    assert.equal(env.fakes[30].sent[before], "REBOOT");
    press(node, "进烧录模式");
    await rerender(env, tree);
    assert.equal(env.fakes[30].sent.at(-2), "BOOTLOADER");
  } finally {
    if (realConfirm === undefined) delete globalThis.confirm;
    else globalThis.confirm = realConfirm;
  }
});

test("扫描控制：汇聚实例只读——每一颗钮都置灰，并说清命令要在场地实例上发", async () => {
  const env = controlEnv({ role: "aggregator" });
  const tree = env.mount(nodeControls.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /汇聚实例只读：命令要在场地实例上发/);
  const all = buttons(node);
  assert.ok(all.length > 20, "控件没画出来，这条判据就是空的");
  assert.deepEqual(all.filter((button) => !button.props.disabled), []);
  const open = fields(node).filter((field) => !field.props.disabled);
  assert.deepEqual(open.map((field) => field.props.class), ["ctl-picker"],
    "只有切板的下拉不置灰：换控制对象是导航，不是写命令");
});

test("扫描控制：切板即切控制对象——读数、草稿都不许跟着串台", async () => {
  const env = controlEnv();
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  clickNode(node, (row) => row.props.title === "整段选中 Boscam A（替换当前选择）");
  node = tree();
  assert.ok(hasClass(byTitle(node, "A1: 5865 MHz"), "on"));

  findByClass(node, "ctl-picker")[0].props.onChange({ target: { value: "31" } });
  env.update();
  node = await rerender(env, tree);
  assert.match(textOf(findByClass(node, "ctl-head")[0]), /板 R2/);
  assert.match(textOf(findByClass(node, "ctl-head")[0]), /节点 31 · 接入机 桥 A · 传输 usb \/dev\/ttyUSB0/);
  assert.equal(findByClass(node, "chip--warn").length, 0, "这块板不停 RH，就不该挂那枚牌子");
  assert.match(textOf(ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）")), /12 \/ 63/);
  assert.equal(hasClass(byTitle(node, "A1: 5865 MHz"), "on"), false, "上一块板的草稿不许带过来");
  assert.deepEqual(env.fakes[31].sent, ["STATUS?", "PROFILE?"]);

  findByClass(node, "ctl-picker")[0].props.onChange({ target: { value: "30" } });
  env.update();
  node = await rerender(env, tree);
  assert.ok(hasClass(byTitle(node, "A1: 5865 MHz"), "on"), "切回来时这一块板自己的草稿还在");
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?", "STATUS?", "PROFILE?"],
    "切板就重读：切回来也不许拿几分钟前的读数当现在的");
});

test("扫描控制：板子拒绝增益时，那一行警告印的是板子的原词", async () => {
  const env = controlEnv({ fakes: { 30: fakeBoard({ replies: { ...R1_REPLIES, "GAIN MANUAL 99": "ERR gain_range" } }) } });
  const tree = env.mount(nodeControls.create);
  let node = await rerender(env, tree);
  fields(ctlRow(node, "接收增益（GAIN，PHY 表索引，不是 dB）"))
    .find((field) => field.props.type === "range")
    .props.onChange({ target: { value: "99" } });
  node = await rerender(env, tree);
  assert.match(textOf(node), /上一条命令没有成功（board_refused）：ERR gain_range/);
});

// === 协议控制台 =================================================================

test("协议控制台：快捷钮的每一条都在固件里真答，且不含要带参数的 SYNC", () => {
  const source = readFileSync(new URL("../../../../firmware/src/proto/commands.c", import.meta.url), "utf8");
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  const quick = findByClass(tree(), "btn--quick").map(textOf);
  assert.equal(quick.length, 14);
  for (const command of quick) {
    assert.ok(source.includes(`"${command}"`), `固件里没有 ${command} 这条裸查询：点了只会白等一个超时`);
  }
  assert.equal(quick.includes("SYNC"), false, "SYNC 要带参数，裸发只会得到 ERR command");
});

test("协议控制台：挂载只读 STATUS?，不问 PROFILE?，也不轮询", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?"]);
  assert.deepEqual(env.widget.lease.timers, []);
});

test("协议控制台：点快捷钮就把问答印在日志里，JSON 折叠着但真在", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  press(node, "PROFILE?");
  node = await rerender(env, tree);
  const lines = findByClass(node, "console-line");
  assert.equal(lines.length, 2);
  assert.match(textOf(lines[0]), /> PROFILE\?/);
  assert.match(textOf(lines[0]), /< PROFILE \{"name":"fpv58"/);
  assert.equal(findByClass(lines[0], "console-json").length, 1);
  assert.match(textOf(lines[0]), /"khz": 5658000/);
  assert.match(textOf(lines[0]), /端点 \/dev\/ttyAMA0 · 12 ms/);
  assert.equal(hasClass(lines[0], "is-bad"), false);
});

test("协议控制台：回车发送，只去首尾空白，不做大小写归一也不替它补参数", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  const input = findByClass(node, "console-input")[0];
  input.props.onInput({ target: { value: "  gain manual 40  " } });
  node = tree();
  findByClass(node, "console-input")[0].props.onKeydown({ key: "Enter", preventDefault() {} });
  node = await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-1), "gain manual 40");
  assert.equal(findByClass(node, "console-input")[0].props.value, "", "发完把草稿清掉");
  assert.match(textOf(findByClass(node, "console-line")[0]), /> gain manual 40/);
});

test("协议控制台：空行不发，发送钮在空草稿时置灰", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  const before = env.fakes[30].sent.length;
  assert.equal(press(node, "发送").props.disabled, true);
  findByClass(node, "console-input")[0].props.onInput({ target: { value: "   " } });
  node = tree();
  findByClass(node, "console-input")[0].props.onKeydown({ key: "Enter", preventDefault() {} });
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.length, before);
});

test("协议控制台：发过设置命令不自动重读（R21），要不要重读由运营者点", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  press(node, "重读板子");
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "STATUS?"]);
  findByClass(node, "console-input")[0].props.onInput({ target: { value: "GAIN MANUAL 40" } });
  press(tree(), "发送");
  await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "STATUS?", "GAIN MANUAL 40"]);
});

test("协议控制台：上下键翻的历史按板分开——在 B 板翻出 A 板的命令，回车就是写错板", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  const type = (text) => {
    findByClass(tree(), "console-input")[0].props.onInput({ target: { value: text } });
    findByClass(tree(), "console-input")[0].props.onKeydown({ key: "Enter", preventDefault() {} });
  };
  type("TIMING?");
  await rerender(env, tree);
  env.actions.selectNode(31);
  env.update();
  node = await rerender(env, tree);
  type("SPURS?");
  node = await rerender(env, tree);

  const key = (name) => findByClass(tree(), "console-input")[0].props.onKeydown({ key: name, preventDefault() {} });
  key("ArrowUp");
  assert.equal(findByClass(tree(), "console-input")[0].props.value, "SPURS?");
  key("ArrowUp");
  assert.equal(findByClass(tree(), "console-input")[0].props.value, "SPURS?", "这块板只发过一条，翻不出别的");
  key("ArrowDown");
  assert.equal(findByClass(tree(), "console-input")[0].props.value, "");

  env.actions.selectNode(30);
  env.update();
  await rerender(env, tree);
  key("ArrowUp");
  assert.equal(findByClass(tree(), "console-input")[0].props.value, "TIMING?");
});

test("协议控制台：日志按板筛着看，切板之后上一块板的回答不留在屏幕上", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  press(node, "INFO");
  node = await rerender(env, tree);
  assert.match(textOf(node), /INFO widscan58\|esp32s3/);
  env.actions.selectNode(31);
  env.update();
  node = await rerender(env, tree);
  assert.equal(textOf(node).includes("esp32s3"), false);
  assert.match(textOf(node), /> STATUS\?/);
  assert.match(textOf(node), /"profile":"scaled24"/);
});

test("协议控制台：ERR 原词一个不改，注解只是加注；认不出的词不编解释", async () => {
  const env = controlEnv({
    plugin: "node-console",
    fakes: { 30: fakeBoard({ replies: { ...R1_REPLIES, "GAIN 999": "ERR gain_range", "RAWIQ 1 16": "ERR uart_port", "WAT?": "ERR wat" } }) },
  });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  const send = async (text) => {
    findByClass(tree(), "console-input")[0].props.onInput({ target: { value: text } });
    press(tree(), "发送");
    return rerender(env, tree);
  };
  node = await send("GAIN 999");
  let line = findByClass(node, "console-line")[0];
  assert.ok(hasClass(line, "is-bad"));
  assert.match(textOf(line), /< ERR gain_range/);
  assert.match(textOf(line), /ERR gain_range：增益索引超出板子报的 gain_max/);
  assert.match(textOf(line), /失败（board_refused）：ERR gain_range/);

  // RAWIQ 拼不出整快照时，后端先判 rawiq_incomplete（`node_control.py` 的 `_result`）：
  // 板子的原词照印，失败原因也不许被 ERR 盖掉。
  node = await send("RAWIQ 1 16");
  line = findByClass(node, "console-line")[0];
  assert.match(textOf(line), /< ERR uart_port/);
  assert.match(textOf(line), /ERR uart_port：这类帧只走 USB 口/);
  assert.match(textOf(line), /失败（rawiq_incomplete）/);

  node = await send("WAT?");
  line = findByClass(node, "console-line")[0];
  assert.match(textOf(line), /< ERR wat/);
  assert.equal(textOf(line).includes("ERR wat："), false, "认不出的词不加注——编一句解释比不解释更贵");
});

test("协议控制台：收到 RAW IQ 快照时明说这一格不画图，去「原始 I/Q」那一格抓", async () => {
  const env = controlEnv({ plugin: "node-console", fakes: { 30: fakeBoard({ rawiq: toneSnapshot({ n: 1024 }) }) } });
  const tree = env.mount(nodeConsole.create);
  let node = await rerender(env, tree);
  findByClass(node, "console-input")[0].props.onInput({ target: { value: "RAWIQ 5658000 1024" } });
  press(node, "发送");
  node = await rerender(env, tree);
  const line = findByClass(node, "console-line")[0];
  assert.match(textOf(line), /RAW IQ 快照 1024 点已收到，但这一格不画图：去「原始 I\/Q」那一格抓/);
  assert.match(textOf(line), /没有文本回复，收到 RAW IQ 快照/);
  assert.equal(findByClass(node, "iq-chart").length, 0);
});

test("协议控制台：命令通路的账印在脚下，并说清这是页面启动时读到的那一份", async () => {
  const env = controlEnv({ plugin: "node-console" });
  const tree = env.mount(nodeConsole.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /命令通路（页面启动时读到的 \/api\/health）：开 · 握着 1 块板 · 发过 7 条 · 超时 0 · 失败 1 · 闲 30 s 自动交还/);

  const off = controlEnv({ plugin: "node-console", health: { control: { enabled: false, holders: 0, commands: 0, timeouts: 0, failures: 0, last_error: "command_timeout", idle_release_s: 30 } } });
  const tree2 = off.mount(nodeConsole.create);
  const node2 = await rerender(off, tree2);
  const note = walk(node2).find((row) => row.tag === "p" && textOf(row).startsWith("命令通路"));
  assert.ok(hasClass(note, "warn"), "通路关着就该用警告的语气");
  assert.match(textOf(node2), /上一条错：command_timeout/);
});

test("协议控制台：勾多块一样拒绝；汇聚实例一样只读", async () => {
  const env = controlEnv({ plugin: "node-console", effective: { nodes: [30, 31] } });
  const tree = env.mount(nodeConsole.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /控制不广播/);
  assert.deepEqual(env.calls, []);

  const ro = controlEnv({ plugin: "node-console", role: "aggregator" });
  const tree2 = ro.mount(nodeConsole.create);
  const node2 = await rerender(ro, tree2);
  assert.deepEqual(buttons(node2).filter((button) => !button.props.disabled), []);
  assert.equal(findByClass(node2, "console-input")[0].props.disabled, true);
});

// === 原始 I/Q ===================================================================

test("原始 I/Q：样本数上限由板子说，超上限的选项置灰而不是发出去等 ERR", async () => {
  const env = controlEnv({ plugin: "node-rawiq" });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  let select = findByClass(node, "ctl-select")[0];
  assert.equal(select.props.value, "8192");
  assert.deepEqual(select.children.map((option) => option.props.disabled), [false, false, false, false]);
  assert.match(textOf(node), /样本上限 16380（板子报的 rawiq_max）/);

  const other = controlEnv({ plugin: "node-rawiq", nodeId: 31 });
  const tree2 = other.mount(nodeRawIq.create);
  node = await rerender(other, tree2);
  select = findByClass(node, "ctl-select")[0];
  assert.equal(select.props.value, "4096", "默认 8192 超了这块板的上限，就落到允许的最大一档");
  assert.deepEqual(select.children.map((option) => option.props.disabled), [false, false, true, true]);
  assert.match(textOf(node), /样本上限 4096/);
});

test("原始 I/Q：读不到 STATUS? 就说上限未知，不假装知道", async () => {
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ replies: { "STATUS?": "ERR busy" } }) } });
  const tree = env.mount(nodeRawIq.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /样本上限未知（还没读到 STATUS\?）/);
});

test("原始 I/Q：联动那一档的 LO 取自它所在那一跳，并把出处印出来", async () => {
  const env = controlEnv({ plugin: "node-rawiq", link: { kind: "channel", id: 6 } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  const pick = byTitle(node, "取 5806000 kHz：第 7 档所在那一跳的 LO（STATUS? 的 plan，板子真在用的调谐值）");
  assert.ok(pick, "第 7 档在第二跳里，LO 该是 5806000 而不是第一跳的 5658000");
  assert.equal(textOf(pick), "用第 7 档的 LO");
  clickNode(node, (row) => row === pick);
  node = tree();
  assert.match(textOf(node), /LO 出处：第 7 档所在那一跳的 LO（STATUS\? 的 plan，板子真在用的调谐值）/);
  assert.equal(findByClass(node, "ctl-num--wide")[0].props.value, "5806000");
});

test("原始 I/Q：plan 里没有那一档就退到 PROFILE? 的中心频率，并说明它不是调谐值", async () => {
  const env = controlEnv({
    plugin: "node-rawiq",
    link: { kind: "channel", id: 2 },
    fakes: { 30: fakeBoard({ replies: { ...R1_REPLIES, "STATUS?": STATUS_FRESH } }) },
  });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  const pick = byTitle(node, "取 5732000 kHz：第 3 档的中心频率（PROFILE?，不是调谐值：板子可能按步长拒绝）");
  assert.ok(pick, "刚上电还没扫过，plan 是空的，只能退到名义中心频率");
  clickNode(node, (row) => row === pick);
  node = tree();
  assert.match(textOf(node), /LO 出处：第 3 档的中心频率/);
});

test("原始 I/Q：没有联动时先用这一帧共用的 LO，并说清它不是某一档的中心", async () => {
  const env = controlEnv({
    plugin: "node-rawiq",
    frames: [samplesFrame({ loKhz: 5700000, channels: [point(0, { loKhz: 5700000 })] })],
  });
  const tree = env.mount(nodeRawIq.create);
  const node = await rerender(env, tree);
  const pick = byTitle(node, "取 5700000 kHz：这一帧共用的 LO（/ws/live，不是某一档的中心频率）");
  assert.ok(pick, "帧 LO 与计划第一跳的 5658000 不同，取错了会被这条抓住");
  assert.equal(textOf(pick), "用板子报的 LO");
});

test("原始 I/Q：什么出处都没有时那颗钮置灰，并说清怎么才有", async () => {
  const env = controlEnv({
    plugin: "node-rawiq",
    fakes: { 30: fakeBoard({ replies: { "STATUS?": STATUS_FRESH, "PROFILE?": "ERR busy" } }) },
  });
  const tree = env.mount(nodeRawIq.create);
  const node = await rerender(env, tree);
  const pick = buttons(node).find((button) => textOf(button) === "用板子报的 LO");
  assert.equal(pick.props.disabled, true);
  assert.match(pick.props.title, /还没有可取的 LO/);
  assert.match(textOf(node), /LO 还没有出处：点上面那颗钮，或自己填/);
  assert.equal(buttons(node).find((button) => textOf(button) === "抓一份").props.disabled, true);
});

test("原始 I/Q：抓一份发的是 RAWIQ <lo> <samples>，抓完不自动续抓", async () => {
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ rawiq: toneSnapshot({ n: 8192 }) }) } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  press(node, "用板子报的 LO");
  node = tree();
  findByClass(node, "ctl-select")[0].props.onChange({ target: { value: "4096" } });
  node = tree();
  const capture = buttons(node).find((button) => textOf(button) === "抓一份");
  assert.equal(capture.props.title, "RAWIQ 5658000 4096");
  clickNode(node, (row) => row === capture);
  node = await rerender(env, tree);
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?", "RAWIQ 5658000 4096"]);
  assert.deepEqual(env.widget.lease.timers, [], "抓一次就是一次占用，不许自己续抓");
  await env.flush();
  assert.equal(env.fakes[30].sent.length, 3);
});

test("原始 I/Q：手填的 LO 出处写作「手填」，发出去的就是填的那个数", async () => {
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ rawiq: toneSnapshot({ n: 1024 }) }) } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  findByClass(node, "ctl-num--wide")[0].props.onInput({ target: { value: "5740000" } });
  node = tree();
  assert.match(textOf(node), /LO 出处：手填/);
  press(node, "抓一份");
  await rerender(env, tree);
  assert.equal(env.fakes[30].sent.at(-1), "RAWIQ 5740000 8192");
});

test("原始 I/Q：标量事实一行里的 ADC 计数都乘过 lsb（不乘就把量程说小了 4 倍）", async () => {
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ rawiq: TINY }) } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  press(node, "用板子报的 LO");
  press(tree(), "抓一份");
  node = await rerender(env, tree);
  assert.equal(
    textOf(findByClass(node, "iq-info")[0]),
    "LO 5658.0 MHz（5658000 kHz） · 12.000 MS/s · 4 样本 · scale ×4（10-bit 计数） · 直流 (4.0, 8.0) · RMS 11.3 · 峰值 16/512"
  );
});

test("原始 I/Q：样本不够一段 FFT 就不画谱，但星座与波形照画", async () => {
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ rawiq: TINY }) } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  press(node, "用板子报的 LO");
  press(tree(), "抓一份");
  node = await rerender(env, tree);
  assert.match(textOf(node), /快照只有 4 点，不够一段 1024 点 FFT/);
  assert.match(textOf(node), /不画一条假底噪/);
  assert.equal(findByClass(node, "iq-chart").length, 2, "谱那一格换成空状态，前两张还在");
  assert.match(textOf(node), /星座与波形用的是传输计数/);
});

test("原始 I/Q：三张图真的画了——星座逐点、波形两条线、谱一条折线", async () => {
  const snapshot = toneSnapshot({ n: 4096, bin: 100 });
  const env = controlEnv({ plugin: "node-rawiq", fakes: { 30: fakeBoard({ rawiq: snapshot }) } });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  press(node, "用板子报的 LO");
  press(tree(), "抓一份");
  node = await rerender(env, tree);

  const realColors = globalThis.getComputedStyle;
  globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => ({ "--s1": "#111111", "--s2": "#222222", "--ink": "#eeeeee", "--accent": "#333333" })[name] || "",
  });
  try {
    const boxes = findByClass(node, "iq-chart").map((canvas) => {
      const box = fakeCanvas({ w: 320, h: 160 });
      canvas.props.ref.value = box;
      return { label: canvas.props["aria-label"], box };
    });
    assert.equal(boxes.length, 3);
    tree();
    const [constel, trace, spec] = boxes;
    assert.equal(constel.box.ops.filter((op) => op[0] === "fillRect").length, snapshot.n, "星座是一个点一个点堆出来的");
    assert.equal(polylineLineTos(trace.box), 2 * (400 - 1), "波形只画前 400 点，I 与 Q 各一条");
    assert.equal(polylineLineTos(spec.box), 1024 - 1);
    const caption = trace.box.ops.find((op) => op[0] === "fillText" && String(op[1]).startsWith("I（"));
    assert.equal(caption[1], "I（#111111）· Q（#222222）· 前 400 点，传输计数", "两条线不许同色");
    assert.match(spec.box.ops.find((op) => op[0] === "fillText" && String(op[1]).startsWith("dBFS"))[1], /抓取跨度 12\.000 MHz/);
    assert.match(constel.box.ops.find((op) => op[0] === "fillText")[1], /I\/Q 星座 · ±\d+ 传输计数/);
  } finally {
    globalThis.getComputedStyle = realColors;
  }
});

test("原始 I/Q：板端声明不支持只说一声，不挡下发——命令的权威是板子", async () => {
  const env = controlEnv({ plugin: "node-rawiq", nodeId: 31 });
  const tree = env.mount(nodeRawIq.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /板端声明这块板不支持 RAW IQ（undeclared）：仍可试发，板子的回答说了算/);
  assert.match(textOf(node), /RAW IQ 帧只走 USB 口，UART 接入的板子会回 ERR uart_port/);
  const pick = buttons(node).find((button) => textOf(button) === "用板子报的 LO");
  assert.equal(pick.props.disabled, false);
});

test("原始 I/Q：分块缺了整快照作废，页面说重抓一次而不是画半张图", async () => {
  const env = controlEnv({ plugin: "node-rawiq" });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  press(node, "用板子报的 LO");
  press(tree(), "抓一份");
  node = await rerender(env, tree);
  assert.match(textOf(node), /上一条命令没有成功（rawiq_incomplete）：RAWIQ 分块缺了中间一块，整快照已作废：重抓一次/);
  assert.match(textOf(node), /这块板还没有快照/);
  assert.equal(findByClass(node, "iq-chart").length, 0);
});

test("原始 I/Q：LO 与样本数的草稿按板分开，切板不带上一块板的频点", async () => {
  const env = controlEnv({ plugin: "node-rawiq" });
  const tree = env.mount(nodeRawIq.create);
  let node = await rerender(env, tree);
  findByClass(node, "ctl-num--wide")[0].props.onInput({ target: { value: "5740000" } });
  findByClass(tree(), "ctl-select")[0].props.onChange({ target: { value: "1024" } });
  node = tree();
  assert.equal(findByClass(node, "ctl-num--wide")[0].props.value, "5740000");

  env.actions.selectNode(31);
  env.update();
  node = await rerender(env, tree);
  assert.equal(findByClass(node, "ctl-num--wide")[0].props.value, "", "B 板的 LO 还没填，不许沿用 A 板的");
  assert.equal(findByClass(node, "ctl-select")[0].props.value, "4096", "样本数也按这块板的上限重算");
  assert.deepEqual(env.widget.lease.timers, []);
});

test("原始 I/Q：勾多块拒绝，汇聚实例只读", async () => {
  const env = controlEnv({ plugin: "node-rawiq", effective: { nodes: [30, 31] } });
  const tree = env.mount(nodeRawIq.create);
  const node = await rerender(env, tree);
  assert.match(textOf(node), /控制不广播/);
  assert.deepEqual(env.calls, []);

  const ro = controlEnv({ plugin: "node-rawiq", role: "aggregator" });
  const tree2 = ro.mount(nodeRawIq.create);
  const node2 = await rerender(ro, tree2);
  assert.deepEqual(buttons(node2).filter((button) => !button.props.disabled), []);
});