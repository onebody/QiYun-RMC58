/**
 * 控制通路的共用件：目标板怎么选、命令怎么发、失败与"读数旧了"怎么说。
 *
 * 这些判据在三格之上：三个格子都从这里拿目标与缓存，所以"一格只写一块板""A 板的数字
 * 不许出现在 B 板的标题下""板子的拒绝不是传输故障"只要在这里钉一次，三格就都跑不掉。
 *
 * 假板与真 `createApi` 见 `support/control-fixture.mjs`：两块板的读数故意不一样，
 * 否则"按板分开存"这条判据用两块相同的板也能绿。
 */

import assert from "node:assert/strict";
import test from "node:test";

import { LOG_CAP, READ_STALE_MS, createControl, explainFailure } from "../components/control.js";
import { controlTarget } from "../components/select.js";
import { BOARD_R1, BOARD_R2, controlEnv, fakeBoard } from "./support/control-fixture.mjs";

// --- 目标板：写侧一格只认一块 -----------------------------------------------------

test("nodes 没勾：跟着四级选择器走，切板即切控制对象", () => {
  const env = controlEnv();
  assert.equal(controlTarget(env.deps, env.widget).board.node_id, 30);
  env.deps.context.state.nodeId = 31;
  const target = controlTarget(env.deps, env.widget);
  assert.equal(target.board.node_id, 31);
  assert.equal(target.blocked, null);
  assert.equal(target.multi, false);
});

test("nodes 勾了一块：固定在那一块，切别处不影响它", () => {
  const env = controlEnv({ effective: { nodes: [31] } });
  assert.equal(controlTarget(env.deps, env.widget).board.node_id, 31);
  env.deps.context.state.nodeId = 30;
  assert.equal(controlTarget(env.deps, env.widget).board.node_id, 31, "勾死了就不跟选择器走");
});

test("nodes 勾了多块：拒绝下发，并把板名与怎么改说出来（不广播、不挑第一块）", () => {
  const env = controlEnv({ effective: { nodes: [30, 31] } });
  const target = controlTarget(env.deps, env.widget);
  assert.equal(target.board, null);
  assert.equal(target.blocked, "multi");
  assert.match(target.text, /单写者/);
  assert.ok(target.text.includes("板 R1") && target.text.includes("板 R2"), target.text);
  assert.match(target.text, /nodes/);
});

test("nodes 勾的节点不在名册里：说出是哪几个，不静默丢掉", () => {
  const env = controlEnv({ effective: { nodes: [30, 99] } });
  const target = controlTarget(env.deps, env.widget);
  assert.deepEqual(target.missing, [99]);
  assert.equal(target.board.node_id, 30, "剩下那一块照样能写");
  const gone = controlEnv({ effective: { nodes: [98, 99] } });
  const blocked = controlTarget(gone.deps, gone.widget);
  assert.equal(blocked.board, null);
  assert.equal(blocked.blocked, "no_board");
  assert.match(blocked.text, /98、99/);
});

test("一块板都没有：控制要有对象才发得出去", () => {
  const env = controlEnv({ boards: [] });
  const target = controlTarget(env.deps, env.widget);
  assert.equal(target.board, null);
  assert.equal(target.blocked, "no_board");
});

// --- 失败的说法 -----------------------------------------------------------------

test("explainFailure：后端说过整句就用它的，没说过才用本地那句", async () => {
  assert.deepEqual(explainFailure({ reason: "rh_would_stop", detail: { reason: "rh_would_stop", detail: "后端写的整句" } }), {
    reason: "rh_would_stop", message: "后端写的整句", detail: { reason: "rh_would_stop", detail: "后端写的整句" },
  });
  assert.match(explainFailure({ reason: "control_disabled" }).message, /RFTRACK_CONTROL_ENABLED=0/);
  assert.equal(explainFailure({ reason: "没见过的词" }).message, "没见过的词", "认不出的 reason 原样带出去，不编一句");
  assert.equal(explainFailure(null).reason, "unknown");
  assert.match(explainFailure({ reason: "request_invalid", fields: [{ field: "command", type: "string_too_short" }] }).message, /command\(string_too_short\)/);
});

// --- 缓存与命令 -----------------------------------------------------------------

test("读数按节点分开存：A 板的增益不许出现在 B 板的缓存里", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.read(30, { profile: true });
  await ctl.read(31, { profile: true });
  assert.equal(ctl.boardOf(30).status.gain, 45);
  assert.equal(ctl.boardOf(31).status.gain, 12);
  assert.equal(ctl.boardOf(30).status.rawiq_max, 16380);
  assert.equal(ctl.boardOf(31).status.rawiq_max, 4096);
  assert.equal(ctl.boardOf(30).profile.channels.length, 8);
  assert.equal(ctl.boardOf(31).profile.channels[0].name, "X1");
  assert.equal(ctl.boardOf(30).ep, "/dev/ttyAMA0");
  assert.equal(ctl.boardOf(31).ep, "/dev/ttyUSB0");
});

test("没读过的板是一份空白，而不是沿用上一块板的数字", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.read(30);
  assert.deepEqual(ctl.boardOf(31), { status: null, profile: null, info: null, rawiq: null, ep: null, readAt: null, changedAt: null });
  assert.equal(ctl.staleText(31), null, "压根没读过就不该说「旧」");
});

test("read：STATUS? 之后才问 PROFILE?；STATUS? 没成功就不白问一句", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.read(30, { profile: true });
  assert.deepEqual(env.fakes[30].sent, ["STATUS?", "PROFILE?"]);

  const bad = controlEnv({ fakes: { 30: fakeBoard({ replies: { "STATUS?": "ERR busy" } }) } });
  const ctl2 = createControl(bad.rt, bad.deps, bad.widget);
  await ctl2.read(30, { profile: true });
  assert.deepEqual(bad.fakes[30].sent, ["STATUS?"], "板子正忙，再问一句只是多占一次串口");
  assert.equal(ctl2.boardOf(30).profile, null);
});

test("INFO 与 RAWIQ 各归各的缓存格；设置类命令成功后记下「改过」", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  // 时钟拧着走：`changedAt > readAt` 这条判据在真实串口上天然成立（一次问答十几毫秒），
  // 但假板是同一毫秒答完的——不拧时钟就验不出"改在 read 之后"这件事。
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    await ctl.read(30);
    const before = ctl.boardOf(30).readAt;
    now += 1000;
    await ctl.send(30, "INFO");
    assert.match(ctl.boardOf(30).info, /^INFO widscan58\|esp32s3\|6\|/);
    assert.equal(ctl.boardOf(30).status.gain, 45, "INFO 不该把 STATUS 缓存冲掉");
    now += 1000;
    await ctl.send(30, "GAIN MANUAL 40");
    assert.ok(ctl.boardOf(30).changedAt > before);
    assert.match(ctl.staleText(30), /刚改过板子/);
    now += 1000;
    await ctl.read(30);
    assert.equal(ctl.staleText(30), null, "重读之后手里这份又是板子现在的样子了");
  } finally {
    Date.now = realNow;
  }
});

test("一格之内命令串行：上一条没答完，下一条不发出去", async () => {
  const env = controlEnv({ fakes: { 30: fakeBoard({ manual: true }) } });
  const ctl = createControl(env.rt, env.deps, env.widget);
  const first = ctl.send(30, "GAIN MANUAL 40");
  const second = ctl.send(30, "GAIN MANUAL 41");
  await env.flush();
  assert.deepEqual(env.fakes[30].sent, ["GAIN MANUAL 40"], "第二条还在队里");
  assert.equal(ctl.busy.value, true);
  assert.equal(env.fakes[30].settle(), "GAIN MANUAL 40");
  await env.flush();
  assert.deepEqual(env.fakes[30].sent, ["GAIN MANUAL 40", "GAIN MANUAL 41"]);
  env.fakes[30].settle();
  await Promise.all([first, second]);
  assert.equal(ctl.busy.value, false);
  assert.deepEqual(ctl.log.value.map((entry) => entry.command), ["GAIN MANUAL 41", "GAIN MANUAL 40"], "日志顺序＝点按钮的顺序");
});

test("日志有界：满了丢最旧，最新那条在最前面", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  // 上限是契约里的 200，不从被测代码导入符号来算期望——否则把上限改小，测试跟着变小，等于没测。
  assert.equal(LOG_CAP, 200, "日志上限就是 200 条");
  for (let i = 0; i < 205; i += 1) await ctl.send(30, `GAIN MANUAL ${i}`);
  assert.equal(ctl.log.value.length, 200);
  assert.equal(ctl.log.value[0].command, "GAIN MANUAL 204", "最新那条在最前");
  assert.equal(ctl.log.value.at(-1).command, "GAIN MANUAL 5", "丢的是最旧的 0…4");
});

test("板子的 ERR 原样交出去：进日志、进失败行，但不当成传输故障", async () => {
  const env = controlEnv({ fakes: { 30: fakeBoard({ replies: { "GAIN MANUAL 99": "ERR gain_range" } }) } });
  const ctl = createControl(env.rt, env.deps, env.widget);
  const answer = await ctl.send(30, "GAIN MANUAL 99");
  assert.equal(answer.ok, false);
  assert.equal(answer.reply, "ERR gain_range");
  assert.equal(ctl.failure.value.reason, "board_refused");
  assert.equal(ctl.failure.value.message, "ERR gain_range");
  assert.equal(ctl.log.value[0].reply, "ERR gain_range");
  assert.equal(ctl.boardOf(30).changedAt, null, "被拒绝的命令不算改过");
});

test("后端拒绝下发：整句用后端的，reason 原样可分支，队列不被一次失败卡死", async () => {
  const env = controlEnv({
    fakes: { 30: fakeBoard({ replies: { REBOOT: { error: { status: 409, reason: "rh_would_stop", detail: "接入这块板会停掉这台接入机的 RotorHazard 计时" } } } }) },
  });
  const ctl = createControl(env.rt, env.deps, env.widget);
  const answer = await ctl.send(30, "REBOOT");
  assert.equal(answer.ok, false);
  assert.equal(answer.reason, "rh_would_stop");
  assert.equal(ctl.failure.value.message, "接入这块板会停掉这台接入机的 RotorHazard 计时");
  const next = await ctl.send(30, "STATUS?");
  assert.equal(next.ok, true);
  assert.equal(ctl.failure.value, null, "成功一条就把上一行失败清掉");
});

test("名册里没有这块板：not_found 也落成一个结果对象，send 永不抛", async () => {
  const env = controlEnv({ boards: [BOARD_R1, BOARD_R2] });
  const ctl = createControl(env.rt, env.deps, env.widget);
  const answer = await ctl.send(99, "STATUS?");
  assert.equal(answer.ok, false);
  assert.equal(answer.reason, "not_found");
  assert.equal(ctl.failure.value.message, "库里没有这个节点或它所属的接入机");
});

test("读数旧了就把旧说出来：判据是距上次读板多久，不是偷偷定时刷新", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.read(30);
  assert.equal(ctl.staleText(30), null);
  const realNow = Date.now;
  try {
    Date.now = () => realNow() + READ_STALE_MS + 1000;
    assert.match(ctl.staleText(30), /距今 61 s/);
    assert.match(ctl.staleText(30), /别的页面或运营者可能已经改过它/);
  } finally {
    Date.now = realNow;
  }
  assert.equal(ctl.staleText(30), null, "时钟拧回来就又不旧了");
});

test("租约归还：缓存与日志清空，之后再发一律不动网络", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.read(30);
  const before = env.calls.length;
  env.widget.lease.release();
  assert.deepEqual(ctl.boards.value, {});
  assert.deepEqual(ctl.log.value, []);
  assert.equal(ctl.failure.value, null);
  assert.equal(ctl.busy.value, false);
  assert.equal(await ctl.send(30, "STATUS?"), null);
  assert.equal(env.calls.length, before, "归还之后不许再发命令");
});

test("没有目标板时 send 直接回 null：不拿 undefined 当节点号去拼 URL", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  assert.equal(await ctl.send(null, "STATUS?"), null);
  assert.equal(await ctl.send(undefined, "STATUS?"), null);
  assert.deepEqual(env.calls, []);
});

test("命令走的是节点命令端点，URL 与体形状与后端对得上", async () => {
  const env = controlEnv();
  const ctl = createControl(env.rt, env.deps, env.widget);
  await ctl.send(30, "GAIN MANUAL 40");
  assert.deepEqual(env.api.audit().calls.at(-1), { method: "POST", path: "/api/nodes/30/command", status: 200 });
  assert.deepEqual(env.calls, [{ nodeId: 30, command: "GAIN MANUAL 40" }]);
});