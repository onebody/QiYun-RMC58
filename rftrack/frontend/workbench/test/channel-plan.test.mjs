/**
 * 频道表与 `CHANNELS` 命令。**第一组判据是跨页面对拍**：站点侧这张表与单机控制台
 * `web/src/model/channelPlan.ts` 必须是同一份频率，否则运营者在站点侧改了计划、
 * 回到单机侧看到的却是另一张表——那种错不会报错，只会让板子扫在别的频点上。
 *
 * 对拍的办法是把那份 TS 的表**当文本解析**（Node 跑不了 TS，也不该为一张表引入转译）：
 * 解析式与被测代码无关，改一边不改另一边就会红。
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { BANDS, DEFAULT_PLAN, MAX_CHANNELS, channelMhz, channelsCommand, normalizePlan, planFromProfile } from "../channel-plan.js";

const TS_URL = new URL("../../../../web/src/model/channelPlan.ts", import.meta.url);
const tsSource = readFileSync(TS_URL, "utf8");

/** 从 TS 源码里抠出那张表：`{ letter: "R", name: "Raceband", mhz: [...] }`。 */
function tsBands() {
  const rows = [];
  const pattern = /\{\s*letter:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*mhz:\s*\[([^\]]+)\]\s*\}/g;
  for (const match of tsSource.matchAll(pattern)) {
    rows.push({ letter: match[1], name: match[2], mhz: match[3].split(",").map((v) => Number(v.trim())) });
  }
  return rows;
}

test("两份频道表逐值对拍：段序、段名、八档频率一个都不许分家", () => {
  const theirs = tsBands();
  assert.equal(theirs.length, 6, "单机侧那张表没解析出来：对拍的前提已经不成立");
  assert.deepEqual(BANDS, theirs);
});

test("档数上限与默认计划也与单机侧同源", () => {
  const max = /export const MAX_CHANNELS = (\d+);/.exec(tsSource);
  assert.ok(max, "单机侧的 MAX_CHANNELS 没找到");
  assert.equal(MAX_CHANNELS, Number(max[1]));
  const defaults = /channels:\s*\[([^\]]+)\]/.exec(tsSource);
  assert.ok(defaults, "单机侧的默认计划没找到");
  assert.deepEqual(DEFAULT_PLAN, defaults[1].split(",").map((v) => v.trim().replace(/"/g, "")));
});

test("channelMhz：认得的回频率，认不出的回 undefined（不猜一个「大概是 R 段」）", () => {
  assert.equal(channelMhz("R1"), 5658);
  assert.equal(channelMhz("R8"), 5917);
  assert.equal(channelMhz("L1"), 5362);
  assert.equal(channelMhz("L8"), 5621);
  assert.equal(channelMhz("A1"), 5865, "Boscam A 是倒着排的：A1 最高频");
  for (const name of ["R0", "R9", "X1", "", "1", "R", "r1"]) {
    assert.equal(channelMhz(name), undefined, `${name} 不该被认出来`);
  }
});

test("normalizePlan：去重、剔掉不认识的名字、最多 8 档、按频率升序", () => {
  assert.deepEqual(normalizePlan(["R2", "R1", "R2"]), ["R1", "R2"]);
  assert.deepEqual(normalizePlan(["R1", "X9", "L1"]), ["L1", "R1"], "低频段排在前面");
  assert.deepEqual(normalizePlan(["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "L1"]), DEFAULT_PLAN, "第 9 档被截掉");
  assert.deepEqual(normalizePlan([]), []);
});

test("normalizePlan：同频保持表内顺序（R7 与 F8 都是 5880）", () => {
  assert.equal(channelMhz("R7"), channelMhz("F8"));
  assert.deepEqual(normalizePlan(["F8", "R7"]), ["R7", "F8"], "Raceband 在表里排在 Fatshark 前面");
});

test("channelsCommand：每一档按它自己的 5.8 GHz 频率下发", () => {
  assert.equal(channelsCommand(["R1", "R2"]), "CHANNELS R1=5658 R2=5695");
  assert.equal(channelsCommand(normalizePlan(["A1", "R1"])), "CHANNELS R1=5658 A1=5865");
});

test("planFromProfile：板子报的名字原样留着，认不出的**单独列出来而不是丢掉**", () => {
  const profile = { channels: [{ id: 0, name: "R1", khz: 5658000 }, { id: 1, name: "X1", khz: 2412000 }, { id: 2, name: "R3", khz: 5732000 }] };
  assert.deepEqual(planFromProfile(profile), { names: ["R1", "X1", "R3"], known: ["R1", "R3"], unknown: ["X1"] });
  assert.deepEqual(planFromProfile(null), { names: [], known: [], unknown: [] });
  assert.deepEqual(planFromProfile({}), { names: [], known: [], unknown: [] });
});

test("站点侧不存 localStorage：浏览器里那份旧计划不许被当成板子的当前状态", () => {
  const source = readFileSync(new URL("../channel-plan.js", import.meta.url), "utf8");
  // 文件头那段注释里就写着"这里没有 localStorage"——先把注释挖掉，否则这条判据被自己的
  // 说明文字骗过去，加了真代码也照样绿。
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  assert.equal(code.includes("localStorage"), false, "计划一律从 PROFILE? 读回，页面只当编辑器");
});