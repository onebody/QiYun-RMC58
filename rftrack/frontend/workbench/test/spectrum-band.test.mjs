/**
 * 频谱带骨架的单测（P0 阶段 3.4）。
 *
 * 这里最有价值的一条是第一条：`MISSING` 那三句是**对仓库现状的断言**，不是作者的记忆。
 * 阶段 4 把丢弃分支拆分、建起栅格、开出 `/api/spectrum` 与 `/ws/spectrum` 之后，
 * 这三条会一条条翻红——翻红的那一刻就是"这个骨架可以升入 `components/index.js` 与
 * `registry.COMPONENTS` 了"的信号。反过来，如果哪天有人在文档里宣布"频谱已上线"而代码没动，
 * 这个文件不会替他背书。
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import * as registry from "../registry.js";
import { IMPLEMENTED, declaredWithoutImpl, implWithoutDeclared } from "../components/index.js";
import { MISSING, STAGE_POINTER, create, notice, plugin_id } from "../components/spectrum-band.js";

const ROOT = new URL("../../../..", import.meta.url);
const read = (rel) => readFileSync(new URL(rel, ROOT), "utf8");

const fakeRt = () => {
  const h = (tag, props, children) => {
    if (props === undefined || Array.isArray(props) || typeof props === "string" || typeof props === "number") {
      children = props;
      props = {};
    }
    return { tag, props, children: children === undefined ? null : children };
  };
  return { h, ref: (v) => ({ value: v }), computed: (fn) => ({ get value() { return fn(); } }), onMounted: () => {}, onUpdated: () => {}, nextTick: (fn) => fn && fn(), watchEffect: (fn) => fn() };
};

const render = (depsRegistry = registry) => {
  const component = create(fakeRt(), { registry: depsRegistry });
  // `setup()` 返回的是**渲染函数**（Vue 的约定），不是 vnode 本身。
  return component.setup({ widget: { plugin_id, x: 0, y: 0, w: 6, h: 3, config: {} } })();
};

const flatten = (node, out = []) => {
  if (node === null || node === undefined) return out;
  if (typeof node === "string") out.push(node);
  else if (Array.isArray(node)) node.forEach((child) => flatten(child, out));
  else if (node.children !== undefined) flatten(node.children, out);
  return out;
};

test("骨架今天确实还缺那三条：逐条对着后端与页面的源码验", () => {
  const capture = read("rftrack/backend/app/services/capture.py");
  assert.match(capture, /def _frame/, "capture.py 的丢弃分支已改名，MISSING 第一条要跟着改");
  assert.match(capture, /"channels" not in decoded/, "丢弃条件不再是「没有 channels 就丢」，那条缺口已经补上了——该升级这个组件");
  assert.ok(/FRAME_NAMES[\s\S]{0,400}spectrum/.test(capture), "频谱帧不在丢弃名单里了？");

  assert.equal(existsSync(new URL("rftrack/backend/app/services/spectrum_grid.py", ROOT)), false,
    "栅格模块已经存在：MISSING 第二条过期，该把 spectrum-band 升入 COMPONENTS");
  assert.equal(read("rftrack/backend/app/models.py").includes("spectrum_capture"), false,
    "spectrum_capture 表已入库：同上");

  const routers = readdirSync(new URL("rftrack/backend/app/routers/", ROOT), { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".py"))
    .map((entry) => read(`rftrack/backend/app/routers/${entry.name}`).includes("spectrum"));
  assert.equal(routers.some(Boolean), false, "后端已经出现带 spectrum 的路由：MISSING 第三条过期");
  assert.equal(read("rftrack/backend/app/routers/ws.py").includes("spectrum"), false, "`/ws/spectrum` 已经开了：同上");
});

test("缺口一条不落全说出来：只报第一条会让人以为补一条就能画图", () => {
  const lines = notice();
  assert.equal(lines.length, MISSING.length, "文案条数与缺口条数不一致，说明有缺口被吞掉");
  for (const entry of MISSING) {
    assert.ok(lines.some((line) => line.includes(entry.text)), `${entry.key} 那条没进文案`);
    assert.match(line_of(lines, entry), /要等：/, `${entry.key} 没说等的是哪一件`);
  }
});
const line_of = (lines, entry) => lines.find((line) => line.includes(entry.text)) || "";

test("骨架不冒充已上线：实现表、注册表、对账两头都要干净", () => {
  assert.equal(plugin_id, "spectrum-band");
  assert.equal(Object.keys(IMPLEMENTED).includes("spectrum-band"), false, "进了实现表却没进注册表 = 死代码；进了注册表却没数据 = 骗人");
  assert.equal(registry.KNOWN_IDS.has("spectrum-band"), false);
  assert.deepEqual(declaredWithoutImpl(), [], "注册表声明了但没装文件");
  assert.deepEqual(implWithoutDeclared(), [], "有实现但注册表没声明");
});

test("排期与措辞只有一个出处：跟着 registry 的 spectrum-view 走", () => {
  const planned = registry.PLANNED.find((entry) => entry.id === STAGE_POINTER);
  assert.ok(planned, "注册表里没有 spectrum-view，这个骨架就失去了出处");
  assert.equal(planned.workspace, "site");
  const text = flatten(render()).join(" ");
  assert.ok(text.includes(planned.stage), "页面上没把等哪个阶段说出来");
  assert.ok(text.includes(planned.name), "页面上没说这一格叫什么");
});

test("说的是「通路还没通」，不是「这块板没读数」", () => {
  const text = flatten(render()).join(" ");
  for (const forbidden of ["暂无数据", "正在加载", "等待数据", "没有采样"]) {
    assert.equal(text.includes(forbidden), false, `把通路缺失说成了设备没读数：${forbidden}`);
  }
  assert.match(text, /取数通路还没通/);
  assert.match(text, /spectrum_grid|栅格/, "没提到栅格，运营者不知道该等哪一块落地");
});

test("注册表少了 spectrum-view 那一条时不崩，且把「失去出处」说出来", () => {
  const text = flatten(render({ PLANNED: [], workspaceWord: (w) => `未知工作区 ${w}` })).join(" ");
  assert.match(text, /失去出处/);
});
