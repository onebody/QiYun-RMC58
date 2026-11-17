/**
 * 控制通路的共用件：一条命令怎么发、板子的回答记在哪、失败怎么说。三个控制格子
 * （`node-controls` / `node-console` / `node-rawiq`）都从这里拿，因为"我现在写的是哪块板、
 * 它上一次答了什么"这件事不许各判各的——三处各存一份缓存，切板之后就会出现
 * "标题写着 B 板、数字是 A 板的"这种最贵的假话。
 *
 * 五条口径：
 *
 * 1. **读数按节点分开存**。切板不是清空重来，而是各看各的：A 板的增益不该在 B 板的标题下
 *    出现，B 板还没读过就该说"还没读"，而不是沿用上一块板的数字。
 * 2. **不轮询**。每读一次板就要占住那条串口，而占着串口 RotorHazard 就起不来（R21）。
 *    所以只在挂载、切板、下发之后与运营者点"重读"时读；后端闲 30 s 自动交还端点。
 *    代价是读数会旧，那就**把旧说出来**（`staleText`），而不是偷偷定时刷新。
 * 3. **板子的拒绝原样交出去**。`ERR …` 是板子说的话，不是传输故障：它进日志、进那一行
 *    警告，但不当成"后端坏了"。后端的 `detail` 已经是中文整句，页面只排版不重译。
 * 4. **一格之内命令串行**。后端每端点一把命令锁，页面这边再排一条队是为了让日志的顺序
 *    与运营者点按钮的顺序一致；`busy` 期间按钮置灰，不让人以为点了没反应而连点。
 * 5. **写闸只看实例角色**。汇聚实例只读（服务端 405 同一句话）；降级布局挡的是改布局，
 *    不该连带挡住"给板子发一条命令"，其余拒绝一律由后端说了算。
 */

import { controlTarget } from "./select.js";
import { empty, sourceBadge } from "./parts.js";

/** 日志上限。与 `api.js` 的调用账同一条纪律：有界，满了丢最旧。 */
export const LOG_CAP = 200;

/** 读数超过这么久就算旧：站点侧一块板可能被别的页面、别的运营者改过。 */
export const READ_STALE_MS = 60_000;

/** 后端没给整句时的兜底说法。`reason` 一律原样带出来，供对日志与排障。 */
const LOCAL_REASON = {
  aggregator_read_only: "汇聚实例只读：命令要在场地实例上发",
  control_disabled: "这台实例关掉了命令下发（RFTRACK_CONTROL_ENABLED=0）",
  rh_would_stop:
    "接入这块板会停掉这台接入机的 RotorHazard 计时：先在单机控制台里挂上它，或换用不停计时的接入方式",
  endpoint_not_found: "桥的端点表里没有这块板的地址：板子可能拔了，或桥重启后换了名字",
  protocol_rh_forbidden: "板卡归属切换走 POST /api/board/mode，不走命令通路",
  command_timeout: "板子没有在规定时间内回答这条命令",
  upstream_gone: "上游在命令途中断开",
  not_found: "库里没有这个节点或它所属的接入机",
  network_unreachable: "后端没有答话",
  rawiq_incomplete: "RAWIQ 分块缺了中间一块，整快照已作废：重抓一次",
};

/** 一次失败 → `{reason, message, detail}`。后端说过整句就用它的，不自己另编一句。 */
export function explainFailure(error) {
  const reason = (error && error.reason) || "unknown";
  const detail = (error && error.detail) || null;
  if (reason === "request_invalid") {
    const fields = ((error && error.fields) || []).map((field) => `${field.field}(${field.type})`).join("、");
    return { reason, message: fields ? `请求体不合法：${fields}` : "请求体不合法", detail };
  }
  const message =
    (detail && detail.detail) || LOCAL_REASON[reason] || (error && error.message) || reason;
  return { reason, message, detail };
}

/** 命令的第一个词：决定这条回答该记进缓存的哪一格。 */
const verbOf = (command) => String(command).trim().split(/\s+/)[0];

/**
 * 一份空白读数。`readAt` 与 `changedAt` 分开记：前者是"我什么时候读过"，后者是"我什么时候
 * 改过"，改在 read 之后就意味着手里这份数字已经是旧的——这与"很久没读"是两句话。
 */
const blankBoard = () => ({
  status: null,
  profile: null,
  info: null,
  rawiq: null,
  ep: null,
  readAt: null,
  changedAt: null,
});

export function createControl(rt, deps, widget) {
  const { ref } = rt;
  const busy = ref(false);
  const failure = ref(null);
  const log = ref([]);
  const boards = ref({});
  let released = false;
  let pending = 0;
  let chain = Promise.resolve();

  const boardOf = (nodeId) => boards.value[nodeId] || blankBoard();

  function patch(nodeId, changes) {
    boards.value = { ...boards.value, [nodeId]: { ...boardOf(nodeId), ...changes } };
  }

  function append(entry) {
    const next = [entry, ...log.value];
    log.value = next.length > LOG_CAP ? next.slice(0, LOG_CAP) : next;
  }

  /** 排进这一格的队。任务自己吞掉异常，所以调用方拿到的永远是一个结果对象。 */
  function enqueue(task) {
    pending += 1;
    busy.value = true;
    const run = chain.then(() => (released ? null : task()));
    chain = run.then(
      () => {},
      () => {}
    );
    return run.then(
      (value) => {
        pending -= 1;
        if (!pending) busy.value = false;
        return value;
      },
      (error) => {
        pending -= 1;
        if (!pending) busy.value = false;
        throw error;
      }
    );
  }

  /**
   * 发一条命令并等板子回答。**永不抛**：失败也回一个 `{ok:false, …}`，
   * 因为三个格子都要把失败画在同一行警告里，而不是各写一遍 try/catch。
   */
  function send(nodeId, command) {
    if (released || nodeId === null || nodeId === undefined) return Promise.resolve(null);
    return enqueue(async () => {
      const at = Date.now();
      let answer;
      try {
        answer = await deps.api.nodeCommand(nodeId, command);
      } catch (error) {
        if (released) return null;
        const explained = explainFailure(error);
        answer = {
          node_id: nodeId,
          command,
          ok: false,
          reply: null,
          data: null,
          rawiq: null,
          ep: null,
          elapsed_ms: null,
          ...explained,
        };
        failure.value = { ...explained, command, node_id: nodeId };
        append({ ...answer, at });
        return answer;
      }
      if (released) return null;
      const verb = verbOf(command);
      const changes = { ep: answer.ep };
      if (answer.ok && answer.data) {
        if (verb === "STATUS?" || verb === "CONFIG?") {
          changes.status = answer.data;
          changes.readAt = Date.now();
        } else if (verb === "PROFILE?") changes.profile = answer.data;
      }
      if (answer.ok && verb === "INFO") changes.info = answer.reply;
      if (answer.ok && verb === "RAWIQ") changes.rawiq = answer.rawiq;
      // 设置类命令成功了就意味着手里那份 STATUS 已经不代表板子：记下"改过"，让页面说旧。
      if (answer.ok && !answer.data && verb !== "RAWIQ") changes.changedAt = Date.now();
      patch(nodeId, changes);
      failure.value = answer.ok
        ? null
        : {
            reason: answer.reason || "board_refused",
            message: answer.detail || answer.reply || "板子拒绝了这条命令",
            command,
            node_id: nodeId,
          };
      const entry = { ...answer, at };
      append(entry);
      return entry;
    });
  }

  /** 读一次板：`STATUS?`（要频道计划时再加 `PROFILE?`）。 */
  async function read(nodeId, { profile = false } = {}) {
    const answer = await send(nodeId, "STATUS?");
    if (profile && answer && answer.ok) await send(nodeId, "PROFILE?");
    return answer;
  }

  /** 手里这份读数还能不能当"板子现在的样子"用。回 null 表示新鲜（或压根还没读过）。 */
  function staleText(nodeId) {
    const board = boardOf(nodeId);
    if (board.readAt === null) return null;
    if (board.changedAt !== null && board.changedAt > board.readAt) {
      return "这一格刚改过板子，下面的读数还是改之前的：重读一次";
    }
    const ageS = Math.round((Date.now() - board.readAt) / 1000);
    if (ageS * 1000 < READ_STALE_MS) return null;
    return `上一次读板距今 ${ageS} s：别的页面或运营者可能已经改过它，重读一次`;
  }

  widget.lease.onRelease(() => {
    released = true;
    boards.value = {};
    log.value = [];
    failure.value = null;
    busy.value = false;
  });

  return { busy, failure, log, boards, boardOf, send, read, staleText, target: () => controlTarget(deps, widget) };
}

/** 目标说不通时那一格说的话。三个格子共用一句，措辞不会分家。 */
export function controlBlocked(rt, target) {
  return empty(rt, target.text, target.multi ? "控制不广播：一条命令只发给一块板" : null);
}

/**
 * 常驻的对象行：**"我现在写的是哪块板"在每一格里都印在同一处**，板名 + 节点号 + 接入机 +
 * 传输方式，窄屏换行也不省略区分位。`nodes` 没勾时给一个板下拉（切板即切控制对象）；
 * 勾死了一块就不给下拉——给了也切不动这一格，那是骗人的旋钮。
 */
export function controlHead(rt, deps, { board, target, ctl, actions = [], note = null }) {
  const { h } = rt;
  const nodeId = board.node_id;
  const gateway = deps.context.state.gateways.find((row) => row.gateway_id === board.gateway_id) || null;
  const last = ctl.log.value.find((entry) => entry.node_id === nodeId) || null;
  const writable = deps.context.state.instanceRole !== "aggregator";
  const stale = ctl.staleText(nodeId);
  return h("div", { class: "ctl-head" }, [
    h("p", { class: "meta" }, [
      sourceBadge(rt, "api"),
      h("strong", {}, board.display_name),
      h("span", { class: "muted" }, [
        `节点 ${nodeId} · 接入机 ${gateway ? gateway.name : board.gateway_id} · 传输 ${board.transport.kind || "未知"}${board.transport.addr ? ` ${board.transport.addr}` : ""}`,
      ]),
      board.stops_rh
        ? h("span", { class: "chip chip--warn", title: "接入这块板会停掉这台接入机的 RotorHazard 计时" }, "会停 RH")
        : null,
      target.multi ? null : nodePicker(rt, deps, board),
    ]),
    h("p", { class: "note" }, writable
      ? "单写者：这一格的命令只发给上面那块板。不轮询——读板会占住串口，闲一会儿后端自动交还。"
      : "汇聚实例只读：命令要在场地实例上发。"),
    note ? h("p", { class: "note" }, note) : null,
    ctl.failure.value
      ? h("p", { class: "warn" }, `上一条命令没有成功（${ctl.failure.value.reason}）：${ctl.failure.value.message}`)
      : null,
    stale ? h("p", { class: "note note--stale" }, stale) : null,
    h("p", { class: "ctl-actions" }, [
      ...actions,
      last && last.ep
        ? h("span", { class: "src" }, `端点 ${last.ep}${last.elapsed_ms === null || last.elapsed_ms === undefined ? "" : ` · ${last.elapsed_ms} ms`}`)
        : null,
    ]),
  ]);
}

function nodePicker(rt, deps, board) {
  const { h } = rt;
  const boards = deps.context.state.scopeNodes;
  if (boards.length < 2) return null;
  return h("select", {
    class: "ctl-picker",
    title: "切到另一块板：这一格跟着切控制对象",
    value: String(board.node_id),
    onChange: (event) => deps.actions.selectNode(Number(event.target.value)),
  }, boards.map((row) =>
    h("option", { value: String(row.node_id), selected: row.node_id === board.node_id }, `${row.display_name}（${row.node_id}）`)
  ));
}