/* WIDSCAN58 Platform Shell — Phase 08.4
 * 只读平台管理界面：真实调用 site-server 现有 REST API。
 * 无 Mock、无假图表、未实现能力显式标注。
 */
(function () {
  "use strict";

  var CONFIG = Object.assign({ consoleUrl: "" }, window.__PLATFORM_CONFIG__ || {});
  var FRAME_TYPES = ["METRICS", "SPECTRUM", "TELEMETRY", "LEVELS", "RAWIQ", "LOG"];
  var INGESTION_LABELS = {
    persisted: "已落库",
    duplicate_events: "重复事件（去重）",
    invalid_event_id: "非法 event_id",
    legacy_uuid_fallback: "旧协议 UUID 兜底",
    derived_event_id: "派生 event_id",
    agent_provided_event_id: "Agent 提供 event_id",
    invalid_agent_timestamp: "非法 Agent 时间戳",
    integrity_errors: "非重复完整性错误"
  };

  var state = {
    cache: new Map(),
    reachable: null,
    lastRefresh: null
  };
  var renderToken = 0;
  var pollTimer = null;

  /* --- DOM helpers ------------------------------------------------------- */

  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "dataset") Object.assign(node.dataset, value);
        else if (key.slice(0, 2) === "on" && typeof value === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else {
          node.setAttribute(key, value === true ? "" : String(value));
        }
      });
    }
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }

  function append(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(function (c) { append(node, c); });
    } else if (child instanceof Node) {
      node.appendChild(child);
    } else {
      node.appendChild(document.createTextNode(String(child)));
    }
  }

  function $(selector) { return document.querySelector(selector); }

  function pageHead(title, desc) {
    return el("div", { class: "pf-page__head" },
      el("div", {},
        el("div", { class: "pf-page__title", text: title }),
        desc ? el("div", { class: "pf-page__desc", text: desc }) : null
      ),
      el("div", { class: "pf-spacer" }),
      el("button", {
        class: "pf-btn",
        type: "button",
        onclick: function () { renderPage({ quiet: true }); }
      }, "刷新")
    );
  }

  function badge(kind, text, title) {
    return el("span", { class: "pf-badge pf-badge--" + kind, text: text, title: title || null });
  }

  function unknown(text) {
    return el("span", { class: "pf-unimplemented", text: text || "未实现" });
  }

  function dash() {
    return el("span", { class: "pf-note", text: "—" });
  }

  function hashLink(route, text) {
    return el("a", { href: "#" + route, text: text });
  }

  /* --- API --------------------------------------------------------------- */

  function ApiError(status, path, message) {
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.message = message;
  }
  ApiError.prototype = Object.create(Error.prototype);

  async function api(path, params) {
    var url = new URL(path, location.origin);
    if (params) {
      Object.keys(params).forEach(function (key) {
        var value = params[key];
        if (value !== null && value !== undefined && value !== "") {
          url.searchParams.set(key, String(value));
        }
      });
    }
    var res;
    try {
      res = await fetch(url.toString(), { headers: { Accept: "application/json" }, cache: "no-store" });
    } catch (err) {
      state.reachable = false;
      updateServerBadge();
      throw new ApiError(0, path, "网络不可达");
    }
    if (!res.ok) {
      state.reachable = res.status < 500;
      updateServerBadge();
      var detail = "";
      try {
        var body = await res.json();
        detail = body && body.detail ? String(body.detail) : "";
      } catch (ignore) { /* 非 JSON 错误体 */ }
      throw new ApiError(res.status, path, detail || res.statusText);
    }
    state.reachable = true;
    state.lastRefresh = new Date();
    updateServerBadge();
    updateRefreshLabels();
    return res.json();
  }

  async function load(key, path, params) {
    try {
      var data = await api(path, params);
      state.cache.set(key, { data: data, ts: Date.now() });
      return { data: data, error: null, stale: false };
    } catch (err) {
      var cached = state.cache.get(key);
      return { data: cached ? cached.data : null, error: err, stale: !!cached };
    }
  }

  function updateServerBadge() {
    var node = $("#pf-server-badge");
    if (!node) return;
    node.className = "pf-badge";
    if (state.reachable === true) {
      node.classList.add("pf-badge--good");
      node.textContent = "Site Server 已连接";
    } else if (state.reachable === false) {
      node.classList.add("pf-badge--critical");
      node.textContent = "Site Server 不可达";
    } else {
      node.classList.add("pf-badge--disabled");
      node.textContent = "状态未知";
    }
  }

  function updateRefreshLabels() {
    var text = state.lastRefresh ? formatClock(state.lastRefresh) : "—";
    ["pf-last-refresh", "pf-status-refresh"].forEach(function (id) {
      var node = $("#" + id);
      if (node) node.textContent = text;
    });
  }

  /* --- Formatting -------------------------------------------------------- */

  function pad(n) { return String(n).padStart(2, "0"); }

  function formatClock(date) {
    return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
  }

  function formatUTC(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) +
      " " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " UTC";
  }

  function timeCell(iso) {
    var text = formatUTC(iso);
    if (!text) return dash();
    return el("span", { class: "pf-mono", text: text, title: String(iso) });
  }

  function msCell(ms) {
    if (ms === null || ms === undefined) return dash();
    return el("span", { class: "pf-mono", text: String(ms) + " ms" });
  }

  function valueCell(value, extraClass) {
    if (value === null || value === undefined || value === "") return dash();
    return el("span", { class: extraClass || "", text: String(value) });
  }

  // 长标识（Event ID 等）：单行显示 + 省略号，完整值放在 title，避免表格被撑高。
  function truncCell(value) {
    if (value === null || value === undefined || value === "") return dash();
    var text = String(value);
    return el("span", { class: "pf-mono pf-trunc", text: text, title: text });
  }

  function agentBadge(agent) {
    if (!agent) return badge("disabled", "未知");
    if (agent.status === "online") {
      var last = agent.last_seen_at ? new Date(agent.last_seen_at).getTime() : null;
      if (last && Date.now() - last > 45000) {
        return badge("warning", "心跳超时", "后端仍标记 online；距最后心跳超过 45 秒");
      }
      return badge("good", "在线");
    }
    if (agent.status === "offline") return badge("disabled", "离线");
    return badge("disabled", agent.status || "未知");
  }

  function deviceBadge(device) {
    if (!device) return badge("disabled", "未知");
    var map = {
      online: ["good", "在线"],
      pending: ["info", "待认领"],
      offline: ["disabled", "离线"],
      error: ["critical", "错误"],
      disabled: ["disabled", "禁用"],
      identity_conflict: ["serious", "身份冲突"]
    };
    var entry = map[device.status] || ["disabled", device.status || "未知"];
    return badge(entry[0], entry[1]);
  }

  function adoptedBadge(device) {
    if (device.adopted === true || device.adopted_at) return null;
    return badge("info", "未认领", "adopted_at 为空");
  }

  function lowConfidenceBadge(device) {
    if (device.identity_confidence !== "low") return null;
    return badge("warning", "只读", "identity_confidence=low，不允许命令");
  }

  /* --- Tables ------------------------------------------------------------ */

  function dataTable(columns, rows) {
    var thead = el("thead", {},
      el("tr", {}, columns.map(function (col) {
        var cls = col.align === "num" ? "is-num" : col.align === "center" ? "is-center" : null;
        return el("th", { class: cls, style: col.width ? "width:" + col.width + ";" : null },
          col.headNode || col.label
        );
      }))
    );
    var tbody = el("tbody", {});
    if (!rows.length) {
      tbody.appendChild(el("tr", {},
        el("td", { class: "is-empty", colspan: columns.length, text: "无数据（接口成功返回空）" })
      ));
    } else {
      rows.forEach(function (row) {
        var tr = el("tr", {});
        columns.forEach(function (col) {
          var cls = [
            col.align === "num" ? "is-num" : col.align === "center" ? "is-center" : "",
            col.wrap ? "is-wrap" : ""
          ].filter(Boolean).join(" ");
          var value = col.render(row);
          var cell = el("td", { class: cls });
          append(cell, value === null || value === undefined ? dash() : value);
          tr.appendChild(cell);
        });
        tbody.appendChild(tr);
      });
    }
    return el("div", { class: "pf-table-wrap" }, el("table", { class: "pf-table" }, thead, tbody));
  }

  function errorBar(result, retry) {
    var err = result.error;
    var tag = err.status ? "HTTP " + err.status : "NET";
    var text = "";
    if (err.status === 404) text = "资源不存在";
    else if (err.status === 401 || err.status === 403) text = "未授权";
    else if (err.status >= 500) text = "服务端错误";
    else if (err.status === 0) text = "网络不可达";
    else text = err.message || "请求失败";
    return el("div", { class: "pf-errorbar" },
      el("span", { class: "pf-errorbar__tag", text: tag }),
      el("span", { text: err.path + " — " + text }),
      result.stale ? el("span", { class: "pf-stale", text: "显示的是上一次成功数据" }) : null,
      el("div", { class: "pf-spacer" }),
      el("button", { class: "pf-btn", type: "button", onclick: retry }, "重试")
    );
  }

  function errorBars(results, retry) {
    var seen = new Set();
    var out = [];
    results.forEach(function (result) {
      if (!result.error) return;
      var key = result.error.path + ":" + result.error.status;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(errorBar(result, retry || function () { renderPage({ quiet: true }); }));
    });
    return out;
  }

  function skeleton() {
    return el("div", { class: "pf-skeleton", "aria-busy": "true" },
      el("div", { class: "pf-skeleton__row" }),
      el("div", { class: "pf-skeleton__row" }),
      el("div", { class: "pf-skeleton__row" })
    );
  }

  function emptyState(title, desc) {
    return el("div", { class: "pf-empty" },
      el("div", { class: "pf-empty__title", text: title }),
      desc ? el("div", { class: "pf-empty__desc", text: desc }) : null
    );
  }

  /* --- Shared builders --------------------------------------------------- */

  function statCard(label, value, sub, href) {
    return el("a", { class: "pf-stat", href: href },
      el("div", { class: "pf-stat__label", text: label }),
      el("div", { class: "pf-stat__value", text: value === null || value === undefined ? "—" : String(value) }),
      el("div", { class: "pf-stat__sub", text: sub || "" })
    );
  }

  function card(title, extra, children) {
    return el("section", { class: "pf-card" },
      el("div", { class: "pf-card__head" },
        el("div", { class: "pf-card__title", text: title }),
        extra || null
      ),
      children
    );
  }

  function kv(items) {
    var dl = el("dl", { class: "pf-kv" });
    items.forEach(function (item) {
      dl.appendChild(el("dt", { text: item[0] }));
      var dd = el("dd", { class: item[2] === "mono" ? "pf-mono" : null });
      append(dd, item[1] === null || item[1] === undefined || item[1] === "" ? dash() : item[1]);
      dl.appendChild(dd);
    });
    return dl;
  }

  function sitesOf(list) {
    return (list || []).filter(Boolean);
  }

  /* --- Pages ------------------------------------------------------------- */

  async function pageOverview(token) {
    var results = await Promise.all([
      load("site", "/api/v1/site"),
      load("agents", "/api/v1/agents"),
      load("devices", "/api/v1/devices"),
      load("stats", "/api/v1/measurements/stats"),
      load("diag", "/api/v1/diagnostics")
    ]);
    if (token !== renderToken) return;
    var site = results[0];
    var agents = results[1];
    var devices = results[2];
    var stats = results[3];
    var diag = results[4];

    if (site.data && site.data.name) $("#pf-site-label").textContent = site.data.name;

    var page = $("#pf-page");
    append(page, pageHead("Overview", "当前实例的 Site、Gateway Agent、Device 与数据状态。"));
    append(page, errorBars(results));

    var counts = (site.data && site.data.counts) || {};
    var agentList = agents.data || [];
    var deviceList = devices.data || [];
    var agentsOnline = agentList.filter(function (a) { return a.status === "online"; }).length;
    var devicesOnline = deviceList.filter(function (d) { return d.status === "online"; }).length;

    append(page, el("div", { class: "pf-grid" },
      statCard("Site", counts.agents_total !== undefined ? 1 : (site.data ? 1 : null),
        site.data ? (site.data.id + (site.data.location ? " · " + site.data.location : "")) : "接口失败",
        "#/sites"),
      statCard("Gateway Agent", agentList.length ? agentsOnline + " / " + agentList.length : (counts.agents_total !== undefined ? (counts.agents_online + " / " + counts.agents_total) : null),
        "在线 / 总数", "#/gateways"),
      statCard("Device", deviceList.length ? devicesOnline + " / " + deviceList.length : (counts.devices_total !== undefined ? (counts.devices_online + " / " + counts.devices_total) : null),
        "在线 / 总数", "#/devices"),
      statCard("待认领 Device", counts.devices_pending !== undefined ? counts.devices_pending : null,
        "adopted_at 为空", "#/devices?only_adopted=false")
    ));

    var statsData = stats.data || {};
    var diagData = diag.data || {};
    var ingestion = diagData.ingestion || {};

    append(page, el("div", { class: "pf-grid pf-grid--2" },
      card("Measurement", null,
        el("div", { class: "pf-stat__value", text: statsData.total === undefined ? "—" : String(statsData.total) }),
        el("div", { class: "pf-note", text: "最近：" }),
        el("div", { class: "pf-mono", text: formatUTC(statsData.last_created_at) || "—" })
      ),
      card("系统状态", null,
        kv([
          ["Site Server", diagData.site_id || (site.data && site.data.id) || unknown("未提供")],
          ["数据库", diagData.db_path || unknown("未提供"), "mono"],
          ["Agent 连接", String((diagData.active_agent_connections || []).length)],
          ["重复事件", String(ingestion.duplicate_events === undefined ? "—" : ingestion.duplicate_events)],
          ["完整性错误", String(ingestion.integrity_errors === undefined ? "—" : ingestion.integrity_errors)]
        ])
      )
    ));

    append(page, card("最近告警", null,
      el("div", { class: "pf-note" },
        "当前无告警数据源：", unknown("未实现（Site Server 未提供告警接口）")
      )
    ));
    return { ms: 10000 };
  }

  async function pageSites(token, ctx) {
    if (ctx.segments.length > 1) return siteDetail(token, ctx, ctx.segments[1]);
    var site = await load("site", "/api/v1/site");
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("Sites", "当前实例为单站点部署；多站点列表接口未提供。"));
    if (site.error) append(page, errorBar(site));
    if (site.data && site.data.name) $("#pf-site-label").textContent = site.data.name;
    if (!site.data) {
      append(page, emptyState("无法读取 Site", "请检查 Site Server 是否可达。"));
      return { ms: 15000 };
    }
    var c = site.data.counts || {};
    append(page, dataTable([
      { label: "Site ID", render: function (s) { return el("a", { href: "#/sites/" + encodeURIComponent(s.id), text: s.id }); } },
      { label: "名称", render: function (s) { return valueCell(s.name); } },
      { label: "位置", render: function (s) { return valueCell(s.location); } },
      { label: "时区", render: function (s) { return valueCell(s.timezone); } },
      { label: "状态", render: function (s) { return valueCell(s.status); } },
      { label: "Agent 在线/总", align: "num", render: function () { return String(c.agents_online !== undefined ? c.agents_online + " / " + c.agents_total : "—"); } },
      { label: "Device 在线/总", align: "num", render: function () { return String(c.devices_online !== undefined ? c.devices_online + " / " + c.devices_total : "—"); } },
      { label: "Measurement", align: "num", render: function () { return c.measurements_total === undefined ? "—" : String(c.measurements_total); } }
    ], [site.data]));
    append(page, el("div", { class: "pf-note", style: "margin-top:12px" },
      "多站点说明：", unknown("未实现（GET /api/v1/sites 未提供）")
    ));
    return { ms: 15000 };
  }

  async function siteDetail(token, ctx, rawId) {
    var siteId = decodeURIComponent(rawId);
    var results = await Promise.all([
      load("site", "/api/v1/site"),
      load("agents:site:" + siteId, "/api/v1/agents", { site_id: siteId })
    ]);
    if (token !== renderToken) return;
    var site = results[0];
    var agents = results[1];
    var page = $("#pf-page");
    append(page, pageHead("Site · " + siteId, "只读详情：基本信息、统计与下属 Gateway Agent。"));
    append(page, errorBars(results));
    if (!site.data) {
      append(page, emptyState("Site 不存在", "后端未返回该站点。"));
      return { ms: 15000 };
    }
    var c = site.data.counts || {};
    append(page, el("div", { class: "pf-grid" },
      statCard("Agent 总数", c.agents_total, "在线 " + (c.agents_online === undefined ? "—" : c.agents_online), "#/gateways"),
      statCard("Device 总数", c.devices_total, "在线 " + (c.devices_online === undefined ? "—" : c.devices_online), "#/devices"),
      statCard("待认领", c.devices_pending, "adopted_at 为空", "#/devices?only_adopted=false"),
      statCard("Measurement", c.measurements_total, "站点累计", "#/measurements")
    ));
    append(page, card("Site 基本信息", null,
      kv([
        ["Site ID", site.data.id, "mono"],
        ["名称", valueCell(site.data.name)],
        ["描述", valueCell(site.data.description)],
        ["位置", valueCell(site.data.location)],
        ["时区", valueCell(site.data.timezone)],
        ["状态", valueCell(site.data.status)],
        ["创建时间", timeCell(site.data.created_at)],
        ["更新时间", timeCell(site.data.updated_at)]
      ])
    ));
    append(page, card("下属 Gateway Agent", null,
      dataTable([
        { label: "Agent ID", render: function (a) { return el("a", { href: "#/gateways/" + encodeURIComponent(a.id), text: a.id }); } },
        { label: "名称", render: function (a) { return valueCell(a.name); } },
        { label: "状态", render: function (a) { return agentBadge(a); } },
        { label: "版本", render: function (a) { return valueCell(a.agent_version); } },
        { label: "Device 数", align: "num", render: function (a) { return String(a.device_count === undefined ? "—" : a.device_count); } },
        { label: "最后心跳", render: function (a) { return timeCell(a.last_seen_at); } }
      ], agents.data || [])
    ));
    return { ms: 15000 };
  }

  async function pageGateways(token, ctx) {
    if (ctx.segments.length > 1) return gatewayDetail(token, ctx, ctx.segments[1]);
    var status = ctx.query.get("status") || "";
    var siteId = ctx.query.get("site") || "";
    var result = await load("agents", "/api/v1/agents", { status: status || null, site_id: siteId || null });
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("Gateways", "Gateway Agent 连接状态、版本与关联设备。"));
    append(page, el("div", { class: "pf-filters" },
      el("div", { class: "pf-field" },
        el("span", { class: "pf-field__label", text: "状态" }),
        el("select", {
          class: "pf-select",
          onchange: function (e) {
            var value = e.target.value;
            location.hash = "#/gateways" + (value ? "?status=" + encodeURIComponent(value) : "");
          }
        },
          el("option", { value: "", text: "全部" }),
          el("option", { value: "online", text: "在线" }),
          el("option", { value: "offline", text: "离线" })
        )
      )
    ));
    var select = page.querySelector(".pf-select");
    if (select) select.value = status;
    if (result.error) append(page, errorBar(result));
    var rows = result.data || [];
    append(page, dataTable([
      { label: "Agent ID", render: function (a) { return el("a", { href: "#/gateways/" + encodeURIComponent(a.id), text: a.id }); } },
      { label: "名称", render: function (a) { return valueCell(a.name); } },
      { label: "状态", align: "center", width: "110px", render: function (a) { return agentBadge(a); } },
      { label: "版本", render: function (a) { return valueCell(a.agent_version); } },
      { label: "平台", render: function (a) { return valueCell(a.platform); } },
      { label: "OS", render: function (a) { return valueCell(a.os_info); } },
      { label: "最后心跳", width: "180px", render: function (a) { return timeCell(a.last_seen_at); } },
      { label: "Device 数", align: "num", render: function (a) { return String(a.device_count === undefined ? "—" : a.device_count); } }
    ], rows));
    return { ms: 5000 };
  }

  async function gatewayDetail(token, ctx, rawId) {
    var agentId = decodeURIComponent(rawId);
    var results = await Promise.all([
      load("agents", "/api/v1/agents"),
      load("devices", "/api/v1/devices")
    ]);
    if (token !== renderToken) return;
    var agents = results[0];
    var devices = results[1];
    var page = $("#pf-page");
    append(page, pageHead("Gateway · " + agentId, "只读详情：Agent 连接信息与关联 Device。"));
    append(page, errorBars(results));
    var agent = (agents.data || []).filter(function (a) { return a.id === agentId; })[0];
    if (!agent) {
      append(page, emptyState("Agent 不存在", "列表接口未返回该 Agent。"));
      return { ms: 5000 };
    }
    var owned = (devices.data || []).filter(function (d) {
      return d.owner_agent_id === agentId;
    });
    append(page, el("div", { class: "pf-detail" },
      card("Agent 身份", null,
        kv([
          ["Agent ID", agent.id, "mono"],
          ["名称", valueCell(agent.name)],
          ["Site", valueCell(agent.site_id)],
          ["状态", agentBadge(agent)],
          ["版本", valueCell(agent.agent_version)],
          ["平台", valueCell(agent.platform)],
          ["OS", valueCell(agent.os_info)],
          ["最后心跳", timeCell(agent.last_seen_at)],
          ["注册时间", timeCell(agent.registered_at)],
          ["凭证状态", unknown("未提供（无 REST 端点）")]
        ])
      ),
      card("关联 Device（owner_agent_id）", null,
        dataTable([
          { label: "Device ID", render: function (d) { return el("a", { href: "#/devices/" + encodeURIComponent(d.id), text: d.id }); } },
          { label: "状态", render: function (d) { return deviceBadge(d); } },
          { label: "固件", render: function (d) { return valueCell(d.firmware_version); } },
          { label: "最后上报", render: function (d) { return timeCell(d.last_seen_at); } }
        ], owned)
      )
    ));
    append(page, el("div", { class: "pf-note", style: "margin-top:12px" },
      "Agent 详情接口：", unknown("未实现（无 GET /api/v1/agents/{id} 与凭证端点）")
    ));
    return { ms: 5000 };
  }

  async function pageDevices(token, ctx) {
    if (ctx.segments.length > 1) return deviceDetail(token, ctx, ctx.segments[1]);
    var status = ctx.query.get("status") || "";
    var onlyAdopted = ctx.query.get("only_adopted");
    var params = { status: status || null };
    if (onlyAdopted === "false") params.only_adopted = false;
    else if (onlyAdopted === "true") params.only_adopted = true;
    var result = await load("devices", "/api/v1/devices", params);
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("Devices", "设备身份、状态、所属 Agent 与最后上报时间。"));
    append(page, el("div", { class: "pf-filters" },
      el("div", { class: "pf-field" },
        el("span", { class: "pf-field__label", text: "状态" }),
        el("select", {
          class: "pf-select",
          onchange: function (e) {
            var value = e.target.value;
            location.hash = "#/devices" + (value ? "?status=" + encodeURIComponent(value) : "");
          }
        },
          el("option", { value: "", text: "全部" }),
          el("option", { value: "online", text: "在线" }),
          el("option", { value: "offline", text: "离线" }),
          el("option", { value: "pending", text: "待认领" }),
          el("option", { value: "error", text: "错误" }),
          el("option", { value: "disabled", text: "禁用" }),
          el("option", { value: "identity_conflict", text: "身份冲突" })
        )
      ),
      el("div", { class: "pf-field" },
        el("span", { class: "pf-field__label", text: "认领" }),
        el("select", {
          class: "pf-select",
          onchange: function (e) {
            var value = e.target.value;
            location.hash = "#/devices" + (value ? "?only_adopted=" + encodeURIComponent(value) : "");
          }
        },
          el("option", { value: "", text: "全部" }),
          el("option", { value: "true", text: "已认领" }),
          el("option", { value: "false", text: "未认领" })
        )
      ),
      el("div", { class: "pf-note" },
        "transport_type 过滤：", unknown("后端入参未生效，未提供")
      )
    ));
    var selects = page.querySelectorAll(".pf-select");
    if (selects[0]) selects[0].value = status;
    if (selects[1]) selects[1].value = onlyAdopted || "";
    if (result.error) append(page, errorBar(result));
    append(page, dataTable([
      { label: "Device ID", render: function (d) { return el("a", { href: "#/devices/" + encodeURIComponent(d.id), text: d.id }); } },
      { label: "名称", render: function (d) { return valueCell(d.display_name); } },
      { label: "硬件", render: function (d) { return valueCell(d.hardware_model); } },
      { label: "固件", render: function (d) { return valueCell(d.firmware_version); } },
      { label: "状态", align: "center", width: "130px", render: function (d) {
          return el("span", { style: "display:inline-flex;gap:4px;align-items:center" }, deviceBadge(d), adoptedBadge(d), lowConfidenceBadge(d));
        } },
      { label: "Owner Agent", render: function (d) { return d.owner_agent_id ? el("a", { href: "#/gateways/" + encodeURIComponent(d.owner_agent_id), text: d.owner_agent_id }) : dash(); } },
      { label: "所有权", render: function (d) { return valueCell(d.ownership_status); } },
      { label: "最后上报", width: "180px", render: function (d) { return timeCell(d.last_seen_at); } },
      { label: "端点", align: "num", render: function (d) { return String((d.endpoints || []).length); } }
    ], result.data || []));
    return { ms: 5000 };
  }

  async function deviceDetail(token, ctx, rawId) {
    var deviceId = decodeURIComponent(rawId);
    var results = await Promise.all([
      load("device:" + deviceId, "/api/v1/devices/" + encodeURIComponent(deviceId)),
      load("meas:device:" + deviceId, "/api/v1/devices/" + encodeURIComponent(deviceId) + "/measurements", { limit: 20 })
    ]);
    if (token !== renderToken) return;
    var device = results[0];
    var meas = results[1];
    var page = $("#pf-page");
    append(page, pageHead("Device · " + deviceId, "只读详情：身份、所有权、端点、连接会话与最近 Measurement。"));
    append(page, errorBars(results));
    if (!device.data) {
      append(page, emptyState("Device 不存在", "后端未返回该设备。"));
      return { ms: 5000 };
    }
    var d = device.data;
    append(page, el("div", { class: "pf-detail" },
      card("身份", null,
        kv([
          ["Device ID", d.id, "mono"],
          ["显示名", valueCell(d.display_name)],
          ["硬件", valueCell(d.hardware_model)],
          ["固件", valueCell(d.firmware_version)],
          ["协议", valueCell(d.protocol_version)],
          ["PHY", valueCell(d.phy_version)],
          ["状态", deviceBadge(d)],
          ["身份置信", valueCell(d.identity_confidence)],
          ["最后上报", timeCell(d.last_seen_at)],
          ["首次发现", timeCell(d.first_seen_at)],
          ["能力", d.capabilities ? JSON.stringify(d.capabilities) : unknown("未提供")]
        ])
      ),
      el("div", {},
        card("所有权", null,
          kv([
            ["Owner Agent", d.owner_agent_id || unknown("未指定"), "mono"],
            ["所有权状态", valueCell(d.ownership_status)],
            ["分配时间", timeCell(d.owner_assigned_at)],
            ["更新时间", timeCell(d.owner_updated_at)],
            ["认领时间", timeCell(d.adopted_at)]
          ])
        ),
        el("div", { style: "height:16px" }),
        card("端点", null,
          dataTable([
            { label: "Agent", render: function (e) { return valueCell(e.agent_id, "pf-mono"); } },
            { label: "传输", render: function (e) { return valueCell(e.transport_type); } },
            { label: "标识", wrap: true, render: function (e) { return valueCell(e.endpoint_identifier); } },
            { label: "状态", render: function (e) { return valueCell(e.status); } },
            { label: "最后连接", render: function (e) { return timeCell(e.last_connected_at); } }
          ], d.endpoints || [])
        )
      )
    ));

    // --- 远程命令卡片（新） ---
    var ownerAgent = d.owner_agent_id;
    var commandResultBox = el("pre", { class: "pf-json", hidden: true });
    var commandBtn = el("button", { class: "pf-btn", type: "button" });
    var modeTimingBtn = el("button", { class: "pf-btn pf-btn--primary", type: "button" }, "切换到计时模式 (RH)");
    var modeMonitorBtn = el("button", { class: "pf-btn", type: "button" }, "切换到扫频模式 (MONITOR)");

    async function runCommand(command, label) {
      modeTimingBtn.disabled = true;
      modeMonitorBtn.disabled = true;
      commandBtn.disabled = true;
      commandResultBox.hidden = false;
      commandResultBox.textContent = "⏳ 发送 " + command + " ...";
      try {
        var res = await fetch("/api/v1/devices/" + encodeURIComponent(deviceId) + "/command", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ command: command })
        });
        var body;
        try { body = await res.json(); } catch (e) { body = { reply: res.statusText }; }
        commandResultBox.textContent = "HTTP " + res.status + "\n" + JSON.stringify(body, null, 2);
        commandResultBox.style.color = res.ok ? "#10b981" : "#ef4444";
      } catch (err) {
        commandResultBox.textContent = "网络错误: " + (err.message || err);
        commandResultBox.style.color = "#ef4444";
      } finally {
        modeTimingBtn.disabled = false;
        modeMonitorBtn.disabled = false;
        commandBtn.disabled = false;
      }
    }
    modeTimingBtn.addEventListener("click", function () { runCommand("PROTOCOL RH", "计时"); });
    modeMonitorBtn.addEventListener("click", function () { runCommand("PROTOCOL MONITOR", "扫频"); });

    var commandInput = el("input", {
      class: "pf-input",
      type: "text",
      placeholder: "自定义命令，如 PROTOCOL?",
      style: "min-width:220px"
    });
    commandBtn.textContent = "发送";
    commandBtn.addEventListener("click", function () {
      var cmd = commandInput.value.trim();
      if (!cmd) return;
      runCommand(cmd);
    });

    append(page, el("div", { style: "height:16px" }));
    var cmdCard;
    if (!ownerAgent) {
      cmdCard = card("远程命令", null,
        el("div", { class: "pf-note" },
          el("strong", { text: "该 Device 尚未分配 Owner Agent，无法下发命令。" }),
          el("div", { style: "margin-top:8px" },
            "先在 ", hashLink("#/devices", "Devices 列表"),
            "里完成认领并关联一个 Gateway Agent。"
          )
        )
      );
    } else {
      cmdCard = card("远程命令", null,
        el("div", { class: "pf-note", style: "margin-bottom:12px" },
          "经 Agent ", el("span", { class: "pf-mono", text: ownerAgent }),
          " 中继到设备。超时 15s。"
        ),
        el("div", { class: "pf-toolbar" }, modeTimingBtn, modeMonitorBtn),
        el("div", { style: "height:12px" }),
        el("div", { class: "pf-toolbar" }, commandInput, commandBtn),
        el("div", { style: "height:8px" }),
        commandResultBox
      );
    }
    append(page, cmdCard);
    append(page, el("div", { style: "height:16px" }));
    append(page, card("连接会话（最近 20）", null,
      dataTable([
        { label: "Session", render: function (s) { return valueCell(s.id, "pf-mono"); } },
        { label: "Agent", render: function (s) { return valueCell(s.agent_id, "pf-mono"); } },
        { label: "Boot ID", render: function (s) { return valueCell(s.boot_id, "pf-mono"); } },
        { label: "连接", render: function (s) { return timeCell(s.connected_at); } },
        { label: "断开", render: function (s) { return timeCell(s.disconnected_at); } },
        { label: "原因", render: function (s) { return valueCell(s.disconnect_reason); } },
        { label: "状态", render: function (s) { return valueCell(s.status); } }
      ], d.sessions || [])
    ));
    append(page, el("div", { style: "height:16px" }));
    append(page, card("最近 Measurement（20）", null,
      dataTable([
        { label: "Frame", render: function (m) { return valueCell(m.frame_type); } },
        { label: "Site Received", render: function (m) { return timeCell(m.created_at); } },
        { label: "Sequence", align: "num", render: function (m) { return m.sequence === null || m.sequence === undefined ? dash() : String(m.sequence); } },
        { label: "Event ID", render: function (m) { return truncCell(m.event_id); } }
      ], meas.data || [])
    ));
    append(page, el("div", { style: "margin-top:12px" },
      el("a", { href: "#/measurements?device=" + encodeURIComponent(deviceId), text: "查看该设备全部 Measurement →" })
    ));
    return { ms: 5000 };
  }

  async function pageMeasurements(token, ctx) {
    var devicesResult = await load("devices", "/api/v1/devices");
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("Measurements", "按 Device 查询。后端未提供站点级列表与时间范围过滤。"));
    if (devicesResult.error) append(page, errorBar(devicesResult));

    var deviceId = ctx.query.get("device") || "";
    var frame = ctx.query.get("frame") || "";
    var offset = Math.max(0, Number(ctx.query.get("offset") || "0") || 0);
    var limit = 100;

    var devices = devicesResult.data || [];
    var selector = el("select", {
      class: "pf-select",
      style: "min-width:260px",
      onchange: function (e) {
        var value = e.target.value;
        location.hash = "#/measurements" + (value ? "?device=" + encodeURIComponent(value) : "");
      }
    }, el("option", { value: "", text: "请选择 Device…" }));
    devices.forEach(function (d) {
      selector.appendChild(el("option", {
        value: d.id,
        text: (d.display_name || d.id) + " (" + d.id + ")"
      }));
    });
    selector.value = deviceId;

    var frameSelect = el("select", {
      class: "pf-select",
      onchange: function (e) {
        var value = e.target.value;
        var next = "#/measurements";
        var qs = [];
        if (deviceId) qs.push("device=" + encodeURIComponent(deviceId));
        if (value) qs.push("frame=" + encodeURIComponent(value));
        location.hash = next + (qs.length ? "?" + qs.join("&") : "");
      }
    }, el("option", { value: "", text: "全部 Frame Type" }));
    FRAME_TYPES.forEach(function (type) {
      frameSelect.appendChild(el("option", { value: type, text: type }));
    });
    frameSelect.value = frame;

    append(page, el("div", { class: "pf-filters" },
      el("div", { class: "pf-field" }, el("span", { class: "pf-field__label", text: "Device" }), selector),
      el("div", { class: "pf-field" }, el("span", { class: "pf-field__label", text: "Frame" }), frameSelect),
      el("div", { class: "pf-note" }, "时间范围：", unknown("未实现（后端未提供时间过滤）"))
    ));

    if (!deviceId) {
      append(page, emptyState("请选择 Device", "Measurement 查询接口按 Device 提供；站点级列表未实现。"));
      return null;
    }

    var result = await load("meas:" + deviceId + ":" + frame + ":" + offset,
      "/api/v1/devices/" + encodeURIComponent(deviceId) + "/measurements",
      { frame_type: frame || null, limit: limit, offset: offset });
    if (token !== renderToken) return;
    if (result.error) append(page, errorBar(result));

    var rows = result.data || [];
    append(page, buildMeasurementTable(rows));

    var pager = el("div", { class: "pf-pager" },
      el("button", {
        class: "pf-btn", type: "button", disabled: offset <= 0,
        onclick: function () { gotoMeasurements(deviceId, frame, Math.max(0, offset - limit)); }
      }, "上一页"),
      el("span", { text: "offset " + offset + " · 本页 " + rows.length + " 条（后端未提供总数）" }),
      el("button", {
        class: "pf-btn", type: "button", disabled: rows.length < limit,
        onclick: function () { gotoMeasurements(deviceId, frame, offset + limit); }
      }, "下一页")
    );
    append(page, pager);
    return null;
  }

  function gotoMeasurements(deviceId, frame, offset) {
    var qs = ["device=" + encodeURIComponent(deviceId)];
    if (frame) qs.push("frame=" + encodeURIComponent(frame));
    if (offset) qs.push("offset=" + String(offset));
    location.hash = "#/measurements?" + qs.join("&");
  }

  function buildMeasurementTable(rows) {
    var headNotes = {
      agent: el("span", {},
        "Agent 接收时间 ",
        el("span", { class: "pf-table__note", text: "（接口未返回）" })
      )
    };
    var columns = [
      { label: "ID", align: "num", width: "72px", render: function (m) { return String(m.id); } },
      { label: "Frame", render: function (m) { return valueCell(m.frame_type); } },
      { label: "Device Timestamp", align: "num", width: "140px", render: function (m) { return msCell(m.device_timestamp_ms); } },
      { label: "Sequence", align: "num", width: "92px", render: function (m) { return m.sequence === null || m.sequence === undefined ? dash() : String(m.sequence); } },
      { headNode: headNotes.agent, label: "Agent Received", width: "170px", render: function () { return dash(); } },
      { label: "Site Received", width: "180px", render: function (m) { return timeCell(m.created_at); } },
      { label: "Agent", render: function (m) { return valueCell(m.agent_id, "pf-mono"); } },
      { label: "Boot ID", render: function (m) { return valueCell(m.device_boot_id, "pf-mono"); } },
      { label: "Event ID", render: function (m) { return truncCell(m.event_id); } },
      { label: "Payload", width: "80px", render: function (m) { return payloadButton(m); } },
      { label: "Source", width: "84px", render: function (m) { return valueCell(m.source); } }
    ];

    var thead = el("thead", {}, el("tr", {}, columns.map(function (col) {
      var cls = col.align === "num" ? "is-num" : null;
      return el("th", { class: cls, style: col.width ? "width:" + col.width + ";" : null }, col.headNode || col.label);
    })));
    var tbody = el("tbody", {});
    if (!rows.length) {
      tbody.appendChild(el("tr", {}, el("td", { class: "is-empty", colspan: columns.length, text: "无数据（接口成功返回空）" })));
    } else {
      rows.forEach(function (m) {
        var tr = el("tr", {});
        columns.forEach(function (col) {
          var cls = [col.align === "num" ? "is-num" : "", col.wrap ? "is-wrap" : ""].filter(Boolean).join(" ");
          var cell = el("td", { class: cls });
          var value = col.render(m);
          append(cell, value === null || value === undefined ? dash() : value);
          tr.appendChild(cell);
        });
        tbody.appendChild(tr);

        var detailRow = el("tr", { hidden: true },
          el("td", { colspan: columns.length, class: "is-wrap" },
            el("pre", { class: "pf-json", text: JSON.stringify(m.payload === undefined ? null : m.payload, null, 2) })
          )
        );
        var button = tr.querySelector(".pf-link--button");
        if (button) {
          button.addEventListener("click", function () {
            detailRow.hidden = !detailRow.hidden;
            button.textContent = detailRow.hidden ? "展开" : "收起";
          });
        }
        tbody.appendChild(detailRow);
      });
    }

    return el("div", { class: "pf-table-wrap" },
      el("table", { class: "pf-table" }, thead, tbody)
    );
  }

  function payloadButton() {
    return el("button", { class: "pf-link--button", type: "button" }, "展开");
  }

  async function pageSystem(token) {
    var results = await Promise.all([
      load("health", "/health"),
      load("diag", "/api/v1/diagnostics"),
      load("stats", "/api/v1/measurements/stats")
    ]);
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("System", "Site Server、数据库、Agent 连接与 ingestion 统计。"));
    append(page, errorBars(results));
    var health = results[0].data;
    var diag = results[1].data;
    var stats = results[2].data;

    append(page, el("div", { class: "pf-grid pf-grid--2" },
      card("运行状态", null,
        kv([
          ["Health", health && health.ok === true ? badge("good", "ok") : unknown("不可用")],
          ["Site ID", (health && health.site_id) || (diag && diag.site_id) || "—", "mono"],
          ["数据库", (diag && diag.db_path) || "—", "mono"],
          ["Agent 连接", diag ? String((diag.active_agent_connections || []).length) : "—"],
          ["Measurement 总量", stats ? String(stats.total) : "—"],
          ["最近数据", stats ? timeCell(stats.last_created_at) : "—"]
        ])
      ),
      card("运行配置", null,
        el("div", { class: "pf-note" },
          "版本信息：", unknown("未实现（后端未提供版本端点）"), el("br"),
          "系统日志：", unknown("未实现（后端未提供日志端点）"), el("br"),
          "运行配置：", unknown("未提供（不做未授权读取）")
        )
      )
    ));

    var counts = (diag && diag.counts) || {};
    append(page, card("计数", null,
      kv([
        ["Agent 在线/总", String(counts.agents_online === undefined ? "—" : counts.agents_online + " / " + counts.agents_total)],
        ["Device 在线/总", String(counts.devices_online === undefined ? "—" : counts.devices_online + " / " + counts.devices_total)],
        ["Device 待认领", String(counts.devices_pending === undefined ? "—" : counts.devices_pending)],
        ["Measurement", String(counts.measurements_total === undefined ? "—" : counts.measurements_total)]
      ])
    ));
    append(page, el("div", { style: "height:16px" }));

    var ingestion = (diag && diag.ingestion) || {};
    var keys = Object.keys(INGESTION_LABELS).filter(function (k) { return typeof ingestion[k] === "number"; });
    var max = keys.reduce(function (acc, k) { return Math.max(acc, ingestion[k]); }, 0);
    var bars = el("div", { class: "pf-bars" });
    keys.forEach(function (key) {
      var value = ingestion[key];
      var width = max > 0 ? Math.round((value / max) * 100) : 0;
      bars.appendChild(el("div", { class: "pf-bar" },
        el("span", { class: "pf-bar__label", text: INGESTION_LABELS[key] }),
        el("span", { class: "pf-bar__track" }, el("span", { class: "pf-bar__fill", style: "width:" + width + "%" })),
        el("span", { class: "pf-bar__value", text: String(value) })
      ));
    });
    append(page, card("Ingestion 统计（进程内累计）", null, bars));
    return { ms: 15000 };
  }

  async function pageConsole(token) {
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("RF Console", "原始 widscan58 Web Console 独立运行，使用原有设备连接链路。"));
    if (CONFIG.consoleUrl) {
      append(page, card("入口", null,
        el("p", {},
          "Console 地址：",
          el("a", { href: CONFIG.consoleUrl, target: "_blank", rel: "noopener", text: CONFIG.consoleUrl })
        ),
        el("p", { class: "pf-note", style: "margin-top:8px" },
          "打开后使用 Web Serial / WebUSB / 串口桥连接设备；平台不内嵌、不注入数据。"
        )
      ));
    } else {
      append(page, emptyState("Console 地址未配置", "部署时设置 window.__PLATFORM_CONFIG__.consoleUrl。"));
    }
    return null;
  }

  function pageNotFound(token, ctx) {
    if (token !== renderToken) return;
    var page = $("#pf-page");
    append(page, pageHead("页面不存在", "未知路由：" + ctx.path));
    append(page, emptyState("404", "请从左侧导航进入有效页面。"));
    return null;
  }

  /* --- Router ------------------------------------------------------------ */

  var PAGES = {
    overview: pageOverview,
    sites: pageSites,
    gateways: pageGateways,
    devices: pageDevices,
    measurements: pageMeasurements,
    system: pageSystem,
    console: pageConsole
  };

  function parseHash() {
    var raw = location.hash.slice(1) || "/overview";
    var qIndex = raw.indexOf("?");
    var pathPart = qIndex >= 0 ? raw.slice(0, qIndex) : raw;
    var queryPart = qIndex >= 0 ? raw.slice(qIndex + 1) : "";
    if (!pathPart || pathPart === "/") pathPart = "/overview";
    return {
      path: pathPart,
      segments: pathPart.split("/").filter(Boolean).map(function (s) { return s; }),
      query: new URLSearchParams(queryPart)
    };
  }

  function setActiveNav(route) {
    document.querySelectorAll(".pf-nav__item").forEach(function (item) {
      if (item.dataset.route === route) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
  }

  async function renderPage(options) {
    var opts = options || {};
    var ctx = parseHash();
    var route = ctx.segments[0] || "overview";
    var fn = PAGES[route] || pageNotFound;
    var token = ++renderToken;

    clearTimeout(pollTimer);
    pollTimer = null;
    setActiveNav(route);

    var page = $("#pf-page");
    page.replaceChildren();
    if (!opts.quiet) page.appendChild(skeleton());

    try {
      var result = await fn(token, ctx);
      if (token !== renderToken) return;
      var loader = page.querySelector(".pf-skeleton");
      if (loader) loader.remove();
      if (result && result.ms) {
        schedulePoll(result.ms, token, function () { renderPage({ quiet: true }); });
      }
    } catch (err) {
      if (token !== renderToken) return;
      var loader = page.querySelector(".pf-skeleton");
      if (loader) loader.remove();
      page.appendChild(el("div", { class: "pf-errorbar" },
        el("span", { class: "pf-errorbar__tag", text: "ERR" }),
        el("span", { text: "页面渲染失败：" + (err && err.message ? err.message : String(err)) })
      ));
    }
  }

  function schedulePoll(ms, token, fn) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(function () {
      if (token !== renderToken) return;
      if (document.hidden) {
        schedulePoll(ms, token, fn);
        return;
      }
      fn();
    }, ms);
  }

  async function initConsoleLink() {
    var link = $("#pf-console-link");
    if (!link) return;
    if (!CONFIG.consoleUrl) {
      link.textContent = "RF Console（未配置）";
      link.removeAttribute("target");
      link.href = "#/console";
      return;
    }
    try {
      var res = await fetch(CONFIG.consoleUrl, { method: "HEAD", cache: "no-store" });
      if (!res.ok) {
        link.textContent = "RF Console（未就绪）";
        link.removeAttribute("target");
        link.href = "#/console";
      }
    } catch (err) {
      link.textContent = "RF Console（未就绪）";
      link.removeAttribute("target");
      link.href = "#/console";
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) renderPage({ quiet: true });
  });
  window.addEventListener("hashchange", function () { renderPage({}); });

  if (!location.hash) {
    history.replaceState(null, "", "#/overview");
  }
  initConsoleLink();
  renderPage({});
})();
