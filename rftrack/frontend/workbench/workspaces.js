/**
 * 工作区清单：这一页（Site Monitoring）与单机控制台（`/app/`）。
 *
 * 为什么要有这一份数据而不是在页面上写两条链接：P0 的"双工作区"里，控制台那一页
 * **不是每台实例都发得出**——`RFTRACK_WEB_DIR` 为空是合法部署形态（`app/main.py`
 * 的 `mount_console` 只把"挂没挂、为什么"写进健康检查，不进 `problems`）。入口若
 * 不管三七二十一给一条链接，运营者点进去看到的是 404，而 404 不会告诉他"这台实例
 * 本来就没打算发这一页"。所以链接的可用性只认 `/api/health` 里那一节答复过的内容：
 * **没答复就说不确认，没说挂上就不跳转**（与 `registry.js` 的"未声明即不支持"同一条纪律）。
 *
 * 反过来，"当前在哪个工作区"也不能只藏在 URL 里：同一套壳、同一台机器上开两个页签，
 * 状态栏不说清是哪一个就会让人按另一边的现场做判断。
 */

/** 两个工作区。`id` 是稳定标识（进状态栏、进 `registry.js` 的 `workspace` 标注）。 */
export const WORKSPACES = [
  { id: "single-node", name: "Single Node Console", zh: "单机控制台", base: "/app/" },
  { id: "site", name: "Site Monitoring", zh: "站点监测" },
];

/** 健康检查里那一节的 `reason` → 页面上那句人话。未列出的 reason 原样带出，不翻译成猜测。 */
const DISABLED_TEXT = {
  disabled: "这台实例没配 RFTRACK_WEB_DIR，控制台不由后端发出",
  no_index_html: "配了目录但里面没有 index.html（web/dist/ 还没构建）",
};

export function workspaceById(id) {
  return WORKSPACES.find((entry) => entry.id === id) || null;
}

/**
 * 当前页是哪一个工作区：按路径判，不按"上次选了啥"判。
 *
 * 刷新与新开页签都必须自己说明白自己是哪一边，所以判据只能来自 URL。这里只认真挂
 * 载前缀（`/app/…`）：`/workbench/`、反代到子路径的 `/rftrack/workbench/`、以及今天
 * 直接把后端根当站点根的部署，全都算 Site Monitoring——**这一页就是它**，其余那一种是
 * 由后端发出去的另一套产物，只有 `/app` 这一个前缀说得清这件事。
 */
export function currentWorkspaceId(pathname) {
  const path = String(pathname || "");
  const prefix = WORKSPACES[0].base.replace(/\/$/, "");
  return path === prefix || path.startsWith(prefix + "/") ? WORKSPACES[0].id : WORKSPACES[1].id;
}

/**
 * 控制台入口的现态：`unknown`（健康还没答话）/ `ready` / `disabled`。
 *
 * `unknown` 单列而不并入 `disabled`，是因为它俩在页面上的话不一样：前者是"这一页还没
 * 问到"，后者是"问到并且说没挂"。把前者写成后者就是把没证据说成结论。
 */
export function consoleState(health) {
  const section = health && health.console;
  if (!section) return { state: "unknown", text: "还没从 /api/health 问到这一节" };
  if (section.mounted) return { state: "ready", text: `由后端发出：${section.dir || "/app/"}` };
  return { state: "disabled", text: DISABLED_TEXT[section.reason] || `未挂载（${section.reason || "没给理由"}）` };
}

/**
 * 入口列表：当前那一个不生成链接（点了只会原地刷新），另一个按现态给链接或给理由。
 * `href` 为空表示这一条不可跳转，页面必须把 `note` 显示出来而不是静默灰掉。
 */
export function workspaceEntries({ pathname = "", health = null } = {}) {
  const current = currentWorkspaceId(pathname);
  return WORKSPACES.map((entry) => {
    if (entry.id === current) return { ...entry, current: true, href: "", note: "当前工作区" };
    const view = consoleState(health);
    return {
      ...entry,
      current: false,
      href: entry.id === "single-node" ? (view.state === "ready" ? entry.base : "") : "./",
      note: entry.id === "single-node" ? view.text : "工作台这一页由 /workbench 发出",
      state: view.state,
    };
  });
}

/** 状态栏那一句"当前工作区"：名字用英文（与页面上两条入口同词），括号里给中文，窄屏不至于看不懂。 */
export function statusPart({ pathname = "" } = {}) {
  const entry = workspaceById(currentWorkspaceId(pathname));
  return {
    label: "工作区",
    value: `${entry.name}（${entry.zh}）`,
    tone: "ok",
  };
}
