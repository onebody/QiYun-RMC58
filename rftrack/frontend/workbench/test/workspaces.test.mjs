/**
 * 双工作区入口单测（P0 阶段 3.2）。
 *
 * 钉的是三条判据，不是三种样式：
 * - 「工作台显示两个工作区入口」——两条入口都在，名字用计划里那两个英文词，
 *   当前这一边**不生成链接**（点了原地刷新却长得像能切走，是假入口）。
 * - 「当前工作区名可见于状态栏」——状态栏那一句里有它，且只来自 URL，不来自"上次选了啥"。
 * - 控制台入口的可跳改性只认 `/api/health` 的答复：没答过是 `unknown`，答"没挂"是
 *   `disabled` 并把理由带出来。把前者写成后者等于把没证据说成结论（R28 那一类）。
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  WORKSPACES,
  consoleState,
  currentWorkspaceId,
  statusPart,
  workspaceById,
  workspaceEntries,
} from "../workspaces.js";

const HEALTH_MOUNTED = { console: { mounted: true, reason: null, dir: "/srv/qiyun/web/dist", index: true } };
const HEALTH_DISABLED = { console: { mounted: false, reason: "disabled", dir: null, index: false } };
const HEALTH_NO_INDEX = { console: { mounted: false, reason: "no_index_html", dir: "/srv/web/dist", index: false } };

test("两个工作区都在，名字用计划里的英文词，站点侧这一页排第二", () => {
  assert.deepEqual(WORKSPACES.map((entry) => entry.name), ["Single Node Console", "Site Monitoring"]);
  assert.equal(workspaceById("single-node").base, "/app/");
  assert.equal(workspaceById("nope"), null);
});

test("当前工作区只由 URL 决定：/app 前缀才是控制台，其余都是这一页", () => {
  assert.equal(currentWorkspaceId("/app/"), "single-node");
  assert.equal(currentWorkspaceId("/app/index.html"), "single-node");
  assert.equal(currentWorkspaceId("/app"), "single-node");
  assert.equal(currentWorkspaceId("/workbench/"), "site");
  // 反代到子路径也必须认得回来（硬约束第 1 条：不许硬编码绝对前缀）
  assert.equal(currentWorkspaceId("/rftrack/workbench/index.html"), "site");
  assert.equal(currentWorkspaceId(""), "site");
  // 后缀不是路径分段的相似前缀不许混进来：/application 不是控制台
  assert.equal(currentWorkspaceId("/application/workbench/"), "site");
});

test("健康检查没答过控制台那一节时说不确认，而不是说没挂", () => {
  for (const health of [null, undefined, {}, { console: undefined }]) {
    assert.equal(consoleState(health).state, "unknown");
  }
  assert.match(consoleState({}).text, /还没从 \/api\/health 问到/);
});

test("问到没挂载时分两种理由，都是人话而不是 reason 字符串", () => {
  assert.equal(consoleState(HEALTH_DISABLED).state, "disabled");
  assert.match(consoleState(HEALTH_DISABLED).text, /RFTRACK_WEB_DIR/);
  assert.match(consoleState(HEALTH_NO_INDEX).text, /index\.html/);
});

test("健康里冒出来的新 reason 原样带出，不翻译成猜测", () => {
  const view = consoleState({ console: { mounted: false, reason: "permission_denied" } });
  assert.equal(view.state, "disabled");
  assert.match(view.text, /permission_denied/);
});

test("控制台挂载了才给链接，链接就是挂载前缀本身", () => {
  const [console1, site] = workspaceEntries({ pathname: "/workbench/", health: HEALTH_MOUNTED });
  assert.equal(console1.href, "/app/");
  assert.equal(console1.current, false);
  assert.equal(console1.state, "ready");
  assert.equal(site.current, true, "这一页是 Site Monitoring");
  assert.equal(site.href, "", "当前工作区不生成链接");
});

test("控制台没挂载时入口仍在，但不可跳且带着理由", () => {
  const [console1] = workspaceEntries({ pathname: "/workbench/", health: HEALTH_DISABLED });
  assert.equal(console1.href, "", "不可跳：否则点进去是一条没有理由的 404");
  assert.match(console1.note, /RFTRACK_WEB_DIR/);
  assert.equal(console1.state, "disabled");
});

test("健康还没回来时也不给链接，但语气是未确认", () => {
  const [console1] = workspaceEntries({ pathname: "/workbench/", health: null });
  assert.equal(console1.href, "");
  assert.equal(console1.state, "unknown");
});

test("从控制台那一侧看回来时，工作台才是可跳的那一边", () => {
  const [console1, site] = workspaceEntries({ pathname: "/app/", health: HEALTH_MOUNTED });
  assert.equal(console1.current, true);
  assert.equal(console1.href, "");
  assert.equal(site.current, false);
  assert.equal(site.href, "./");
});

test("状态栏那一句里带着当前工作区的名字，窄屏也能看清是哪一边", () => {
  const onSite = statusPart({ pathname: "/workbench/" });
  assert.equal(onSite.label, "工作区");
  assert.match(onSite.value, /Site Monitoring/);
  assert.match(onSite.value, /站点监测/, "另给一句中文：只写英文词的话窄屏上分不清");
  assert.match(statusPart({ pathname: "/app/" }).value, /Single Node Console/);
});
