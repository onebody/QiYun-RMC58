/**
 * 假 Vue 与假 DOM 小件：**只在这里定义一次**。
 *
 * 原先这些件住在 `components.test.mjs` 里，控制三格的测试又要同一套——两份副本的坏处
 * 与 `live-fixture.mjs` 文件头说的是同一件事：其中一份会先过期，而过期那一份还一直绿。
 *
 * 三个假件都故意笨：`h` 只搭树不 diff，`ref` 只存值不触发渲染，定时器只记账不流逝。
 * 要钉的是"我们对着这些事实写了什么判据"，不是重演 Vue 的响应式。
 */

import assert from "node:assert/strict";

/** 假 Vue 命名空间。`hooks` 与 `refs` 是测试用的旁路：挂载/更新钩子与画布都要能手动摸到。 */
export function fakeRt() {
  const h = (tag, props, children) => {
    if (Array.isArray(props) || typeof props === "string" || typeof props === "number") {
      children = props;
      props = {};
    }
    return { tag, props: props || {}, children: children === undefined ? null : children };
  };
  const hooks = { mounted: [], updated: [] };
  const refs = [];
  return {
    h,
    /** 记下每个 `ref`：绘制路径要能把画布"挂"上去，否则 `paint()` 永远走 early return，测不到。 */
    ref: (value) => { const r = { value }; refs.push(r); return r; },
    computed: (fn) => ({ get value() { return fn(); } }),
    onMounted: (fn) => hooks.mounted.push(fn),
    onUpdated: (fn) => hooks.updated.push(fn),
    nextTick: (fn) => { if (fn) fn(); return Promise.resolve(); },
    watchEffect: (fn) => fn(),
    hooks,
    refs,
  };
}

/** 只记录调用序列的 2D 上下文：够 `chart.draw()` 跑完，不需要真画像素。 */
export function fakeCanvas({ w = 240, h = 120 } = {}) {
  const ops = [];
  const ctx = new Proxy({ ops }, {
    get(target, key) {
      if (key in target) return target[key];
      if (key === "canvas") return { width: w, height: h };
      return (...args) => { ops.push([key, ...args]); };
    },
    set(target, key, value) { target[key] = value; return true; },
  });
  return { clientWidth: w, clientHeight: h, style: {}, getContext: () => ctx, ops };
}

/** `paint()` 会从样式表取色；这是浏览器才有的那一格，Node 里给一个空实现即可（取色有 fallback）。 */
export function stubComputedStyle() {
  if (!globalThis.getComputedStyle) {
    globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  }
}

export const walk = (node) => {
  if (node === null || node === undefined) return [];
  if (Array.isArray(node)) return node.flatMap(walk);
  if (typeof node !== "object" || !node.tag) return [];
  return [node, ...walk(node.children)];
};

/** 把树上所有文字拼起来：文案判据（不是频谱、系统不提供）就在这里断。 */
export const textOf = (node) => {
  const parts = [];
  const visit = (value) => {
    if (typeof value === "string" || typeof value === "number") parts.push(String(value));
    else if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") visit(value.children);
  };
  visit(node);
  return parts.join(" ");
};

export const hasClass = (node, name) => {
  const value = node.props.class;
  if (typeof value === "string") return value.split(/\s+/).includes(name);
  if (Array.isArray(value)) return value.filter(Boolean).some((part) => part.split(/\s+/).includes(name));
  return false;
};

export const findByClass = (root, name) => walk(root).filter((node) => hasClass(node, name));

/** 按文字找节点（按钮/标签）：控制格里"点哪一颗"是按它写的那句话认的，不是按位置。 */
export const findByText = (root, text) => walk(root).filter((node) => textOf(node) === text);

/**
 * 点一个节点。`event` 覆盖默认事件对象：输入框要给 `target.value`，勾选框要给
 * `target.checked`，键盘要给 `key`——默认那份只够点一颗普通按钮。
 */
export function clickNode(root, matcher, event = {}) {
  const target = walk(root).find((node) => node.props && matcher(node));
  assert.ok(target, "要点的节点没找到：结构变了，判据也就断了");
  assert.equal(typeof target.props.onClick, "function", "这个节点没有 onClick，等于点不动");
  target.props.onClick({
    target: { checked: true, value: "" },
    stopPropagation() {},
    preventDefault() {},
    ...event,
    target: { checked: true, value: "", ...(event.target || {}) },
  });
  return target;
}

/** 只记账的租约：`release()` 把归还钩子跑一遍，"销毁成对归还"因此可以逐条断言。 */
export function fakeLease() {
  const timers = [];
  const hooks = [];
  const subs = [];
  return {
    timers,
    hooks,
    setTimeout(fn, ms) { timers.push({ fn, ms, kind: "timeout" }); return timers.length; },
    setInterval(fn, ms) { timers.push({ fn, ms, kind: "interval" }); return timers.length; },
    clearTimeout() {},
    subscribe(topic, handler) { subs.push({ topic, handler }); return () => subs.splice(subs.findIndex((row) => row.handler === handler), 1); },
    onRelease(fn) { hooks.push(fn); return () => hooks.splice(hooks.indexOf(fn), 1); },
    live: () => timers.length + subs.length,
    release() { hooks.forEach((fn) => fn()); timers.length = 0; subs.length = 0; },
  };
}

export const fakeWidget = (pluginId, overrides = {}) => ({
  instance_id: `${pluginId}-1`,
  plugin_id: pluginId,
  x: 0, y: 0, w: 6, h: 4,
  sort: 0,
  config: {},
  server_config: {},
  effective: {},
  declaration_error: null,
  missing: false,
  isNew: false,
  lease: fakeLease(),
  ...overrides,
});