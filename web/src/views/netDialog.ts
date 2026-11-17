// "Device management" dialog: WiFi relay config, AP control, C5 mode switch.
// GPL-3.0-or-later.
import { el } from "../util/canvas";
import { t } from "../i18n";

export interface NetStatus {
  on: boolean;             // the relay is meant to be dialing
  sta: "up" | "down";
  ip?: string;
  ssid?: string;
  rssi?: number;
  ws: "up" | "down" | "dial";
  url?: string;
  id?: string;
  ap?: "up" | "down";      // undefined on firmware that predates the field
  head?: "up" | "down";    // C5 link over UART
  last?: string;           // why the STA/dial is not up
}

export interface DeviceMgmtHost {
  /** Send a raw command line, return its reply (the "OK"/"ERR" or the leading-text line). */
  send(cmd: string): Promise<string | null>;
  /** Whether this dialog actually has a device to talk to. */
  connected(): boolean;
}

/** Same set the relay accepts for ?id=: anything else breaks the URL. */
const ID_RE = /^[A-Za-z0-9_.:-]+$/;

/** Normalize a value that may be a boolean or "up"/"down" string. */
function isUp(v: unknown): boolean {
  return v === true || v === "up" || v === 1;
}
function isDown(v: unknown): boolean {
  return v === false || v === "down" || v === 0;
}

/** "why" alone reads like a guess; "why (201)" points at the STA reason code. */
function statusReason(j: Record<string, unknown>): string | undefined {
  const why = typeof j.why === "string" && j.why && j.why !== "up" ? j.why : undefined;
  const code = typeof j.sta_reason === "number" && j.sta_reason ? j.sta_reason : undefined;
  if (why && code !== undefined) return `${why} (${code})`;
  if (why) return why;
  if (code !== undefined && !isUp(j.sta)) return `reason ${code}`;
  return undefined;
}

/** Try parse a NET STATUS reply. Returns null if the reply isn't parseable. */
export function parseNetStatus(reply: string | null): NetStatus | null {
  if (!reply) return null;
  // Format: "NET {...}" — the JSON object starts after the "NET " prefix.
  const idx = reply.indexOf("{");
  if (idx < 0) return null;
  try {
    const j = JSON.parse(reply.slice(idx)) as Record<string, unknown>;
    const ip = typeof j.ip4 === "string" ? j.ip4 : typeof j.ip === "string" ? j.ip : undefined;
    const out: NetStatus = {
      on: !isDown(j.on),
      sta: isUp(j.sta) ? "up" : "down",
      // ws down while the relay still wants the link = dialing.
      ws: isUp(j.ws) ? "up" : isDown(j.on) ? "down" : "dial",
      url: typeof j.url === "string" && j.url ? j.url : undefined,
      id: typeof j.id === "string" && j.id ? j.id : undefined,
      last: statusReason(j),
    };
    if (out.sta === "up") {
      if (ip && ip !== "none") out.ip = ip;
      if (typeof j.ssid === "string") out.ssid = j.ssid;
      if (typeof j.rssi === "number") out.rssi = j.rssi;
    }
    if (typeof j.ap === "boolean") out.ap = j.ap ? "up" : "down";
    if (typeof j.head_ready === "boolean") out.head = j.head_ready ? "up" : "down";
    return out;
  } catch { return null; }
}

export function parseNetScan(reply: string | null): string[] {
  if (!reply) return [];
  const idx = reply.indexOf("[");
  const end = reply.lastIndexOf("]");
  if (idx < 0 || end < 0) return [];
  try { return JSON.parse(reply.slice(idx, end + 1)); } catch { return []; }
}

export class NetDialog {
  readonly el: HTMLDialogElement;
  private host: DeviceMgmtHost | null = null;
  private busy = false;
  private pollTimer = 0;
  private prefilled = false;

  // --- Network status ---
  private staChip = el("span", { class: "chip" }, "—");
  private apChip = el("span", { class: "chip" }, "—");
  private wsChip = el("span", { class: "chip" }, "—");
  private headChip = el("span", { class: "chip" }, "—");
  private ipVal = el("span");
  private urlVal = el("span");
  private idVal = el("span");
  private lastVal = el("span");
  private statusBtn = el("button", { type: "button" }, "Refresh status");

  // --- WiFi settings form ---
  private ssidSel = el("select", { class: "net-ssid-sel" }) as HTMLSelectElement;
  private ssidMan = el("input", { class: "net-ssid-man", placeholder: "Or type manually" }) as HTMLInputElement;
  private passIn = el("input", { type: "password", class: "net-pass", placeholder: "Wi-Fi password" }) as HTMLInputElement;
  private urlIn = el("input", { class: "net-url", placeholder: "ws://<host>:8080/node?id=<relay-id>" }) as HTMLInputElement;
  private idIn = el("input", { class: "net-id", placeholder: "relay id (optional)" }) as HTMLInputElement;
  private saveLog = el("div", { class: "net-save-log" });

  // --- Actions ---
  private scanBtn = el("button", { type: "button" }, "Scan APs");
  private saveBtn = el("button", { class: "primary", type: "button" }, "Save & Connect");
  private apBtn = el("button", { type: "button" }, "Start AP mode");
  private stopApBtn = el("button", { type: "button" }, "Stop AP mode");

  // --- C5 mode ---
  private protoChip = el("span", { class: "chip" }, "—");
  private monBtn = el("button", { type: "button" }, "PROTOCOL MONITOR");
  private rhBtn = el("button", { type: "button" }, "PROTOCOL RH");

  constructor() {
    const kv = (label: string, value: HTMLElement) =>
      el("div", { class: "kv wide" }, el("span", {}, label), value);

    this.el = el(
      "dialog",
      { class: "net-dialog" },
      el("form", { method: "dialog" },
        el("h2", {}, "Device management"),
        el("p", { class: "net-subtitle" }, "Configure the relay's Wi-Fi, softAP and C5 scanner mode"),
        el("div", { class: "net-body" },
          // Left: what the operator edits.
          el("div", { class: "net-col" },
            el("section", { class: "net-section" },
              el("h3", {}, "Wi-Fi settings"),
              el("label", {}, "SSID", this.ssidSel),
              this.ssidMan,
              el("label", {}, "Password", this.passIn),
              el("label", {}, "Server URL"),
              this.urlIn,
              el("label", {}, "Relay ID (optional)"),
              this.idIn,
              el("p", { class: "hint" },
                "Empty means the relay names itself after its MAC; a URL that already has id= wins. " +
                "A rename applies on the next reconnect, so this page may lose the link."),
              el("div", { class: "net-actions" }, this.scanBtn, this.saveBtn),
              this.saveLog,
            ),
          ),
          // Right: what the relay reports.
          el("div", { class: "net-col" },
            el("section", { class: "net-section" },
              el("h3", {}, "Network status"),
              el("div", { class: "net-status-grid" },
                el("div", { class: "kv" }, el("span", {}, "STA"), this.staChip),
                el("div", { class: "kv" }, el("span", {}, "SoftAP"), this.apChip),
                el("div", { class: "kv" }, el("span", {}, "Bridge"), this.wsChip),
                el("div", { class: "kv" }, el("span", {}, "C5 link"), this.headChip),
                kv("IP", this.ipVal),
                kv("URL", this.urlVal),
                kv("ID", this.idVal),
                kv("Last", this.lastVal),
              ),
              el("div", { class: "net-actions" }, this.statusBtn),
            ),
            el("section", { class: "net-section" },
              el("h3", {}, "SoftAP fallback"),
              el("p", { class: "hint" },
                "The relay opens a softAP (SSID Widscan58-<chip>) when it has no saved Wi-Fi, " +
                "when the STA stays down for 10 s, or after ~90 s of failing to reach the server. " +
                "Join it and open http://192.168.4.1 to configure. Starting it here briefly drops this page's link."),
              el("div", { class: "net-actions" }, this.apBtn, this.stopApBtn),
            ),
            el("section", { class: "net-section" },
              el("h3", {}, "C5 scanner mode"),
              el("div", { class: "kv" }, el("span", {}, "Current"), this.protoChip),
              el("div", { class: "net-actions" }, this.monBtn, this.rhBtn),
              el("p", { class: "hint" }, "MONITOR = spectrum scanning (this dashboard). RH = RotorHazard lap timer."),
            ),
          ),
        ),
        el("div", { class: "dialog-footer" },
          el("button", { value: "close" }, "Close"),
        ),
      ),
    ) as HTMLDialogElement;
    this.build();
  }

  private build() {
    this.statusBtn.onclick = () => void this.refresh();
    this.scanBtn.onclick = () => void this.doScan();
    this.saveBtn.onclick = () => void this.doSave();
    this.apBtn.onclick = () => void this.doAp(true);
    this.stopApBtn.onclick = () => void this.doAp(false);
    this.monBtn.onclick = () => void this.doProto("MONITOR");
    this.rhBtn.onclick = () => void this.doProto("RH");
    // Picking a scanned AP is an explicit choice; the typed override must not win silently.
    this.ssidSel.onchange = () => { if (this.ssidSel.value) this.ssidMan.value = ""; };
    this.el.addEventListener("close", () => {
      this.host = null;
      this.stopPoll();
    });
  }

  async open(host: DeviceMgmtHost) {
    this.host = host;
    this.el.showModal();
    this.reset();
    await this.refresh();
    // The relay's dial state changes on its own; keep the panel honest while it is open.
    if (!this.pollTimer && this.el.open) {
      this.pollTimer = window.setInterval(() => void this.refresh(true), 3000);
    }
  }

  private stopPoll() {
    if (this.pollTimer) window.clearInterval(this.pollTimer);
    this.pollTimer = 0;
  }

  private reset() {
    this.prefilled = false;
    this.busy = false;
    this.ssidSel.replaceChildren(new Option(t("(none — click Scan)"), ""));
    this.ssidMan.value = "";
    this.passIn.value = "";
    this.urlIn.value = "";
    this.idIn.value = "";
    this.setLog("", false);
    this.setChip(this.staChip, "q", "—");
    this.setChip(this.apChip, "q", "—");
    this.setChip(this.wsChip, "q", "—");
    this.setChip(this.headChip, "q", "—");
    this.setChip(this.protoChip, "q", "—");
    this.ipVal.textContent = "";
    this.urlVal.textContent = "";
    this.idVal.textContent = "";
    this.lastVal.textContent = "";
  }

  private setChip(chip: HTMLElement, state: string, text: string) {
    chip.textContent = text;
    chip.classList.remove("up", "down", "dial", "q");
    chip.classList.add(state === "q" ? "q" : state);
  }

  private setLog(text: string, err: boolean) {
    this.saveLog.textContent = text;
    this.saveLog.classList.toggle("err", err);
  }

  private disabled(v: boolean) {
    [this.scanBtn, this.saveBtn, this.apBtn, this.stopApBtn, this.statusBtn, this.monBtn, this.rhBtn]
      .forEach((b) => (b as HTMLButtonElement).disabled = v);
  }

  private async refresh(quiet = false) {
    const host = this.host;
    if (!host || !host.connected() || this.busy) return;
    this.busy = true;
    // A background poll must not disable the buttons under the operator's cursor.
    if (!quiet) this.disabled(true);
    try {
      const [netReply, protoReply] = await Promise.all([
        host.send("NET STATUS"),
        host.send("PROTOCOL?"),
      ]);
      if (this.host !== host) return; // closed or reopened while waiting
      const s = parseNetStatus(netReply);
      if (s) this.renderStatus(s);
      else if (netReply === null) this.lastVal.textContent = t("No reply — the relay may have dropped");

      const p = protoReply && !protoReply.startsWith("ERR")
        ? protoReply.trim().split(/\s+/).pop()?.toUpperCase() ?? ""
        : "";
      this.setChip(this.protoChip, p ? "up" : "q", p || "—");
    } finally {
      this.busy = false;
      if (!quiet) this.disabled(false);
    }
  }

  private renderStatus(s: NetStatus) {
    this.setChip(this.staChip, s.sta, s.sta === "up" ? t("connected") : t("down"));
    this.setChip(this.apChip, s.ap ?? "q",
      s.ap === "up" ? t("AP open") : s.ap === "down" ? t("closed") : "—");
    this.setChip(this.wsChip, s.ws,
      s.ws === "up" ? t("connected") : s.ws === "dial" ? t("connecting") : t("down"));
    this.setChip(this.headChip, s.head ?? "q",
      s.head === "up" ? t("connected") : s.head === "down" ? t("not connected") : "—");

    const ssid = s.ssid ?? "";
    this.ipVal.textContent = s.ip
      ? `${s.ip}${ssid ? ` · ${ssid}` : ""}${s.rssi !== undefined ? ` (${s.rssi} dBm)` : ""}`
      : "";
    this.urlVal.textContent = s.url ?? "";
    this.idVal.textContent = s.id || t("(named after the MAC)");
    this.lastVal.textContent = s.last ?? "";

    if (!this.prefilled) {
      this.prefilled = true;
      if (ssid) this.ssidMan.value = ssid;
      if (s.url) this.urlIn.value = s.url;
      if (s.id) this.idIn.value = s.id;
    }
  }

  private async doScan() {
    const host = this.host;
    if (!host || this.busy) return;
    this.busy = true;
    this.disabled(true);
    this.scanBtn.textContent = t("Scanning…");
    try {
      const reply = await host.send("NET SCAN");
      if (reply === null) this.setLog(t("No reply — the relay may have dropped"), true);
      else if (reply.startsWith("ERR")) this.setLog(reply, true);
      const aps = parseNetScan(reply);
      this.ssidSel.replaceChildren();
      if (aps.length === 0) {
        this.ssidSel.append(new Option(t("(no APs found — check Wi-Fi is on)"), ""));
      } else {
        this.ssidSel.append(new Option(t("(none — click Scan)"), ""));
        // Deduplicate while preserving scan order (best RSSI first).
        const seen = new Set<string>();
        for (const a of aps) {
          if (seen.has(a)) continue;
          seen.add(a);
          this.ssidSel.append(new Option(a, a));
        }
      }
    } finally {
      this.busy = false;
      this.scanBtn.textContent = t("Scan APs");
      this.disabled(false);
    }
  }

  private async doSave() {
    const host = this.host;
    if (!host || this.busy) return;
    const ssid = this.ssidSel.value || this.ssidMan.value.trim();
    const pass = this.passIn.value;
    const url = this.urlIn.value.trim();
    const id = this.idIn.value.trim();
    if (!ssid || !url) {
      this.setLog(t("SSID and URL are required"), true);
      return;
    }
    if (id && !ID_RE.test(id)) {
      this.setLog(t("Relay ID may only use letters, digits, dot, colon, underscore and dash"), true);
      return;
    }
    const lines = [`NET SSID ${ssid}`];
    if (pass) lines.push(`NET PASS ${pass}`);
    lines.push(`NET URL ${url}`);
    if (id) lines.push(`NET ID ${id}`);
    lines.push("NET SAVE", "NET ON");

    this.busy = true;
    this.disabled(true);
    this.setLog(t("Saving…"), false);
    let fail: string | null = null;
    try {
      for (const cmd of lines) {
        const r = await host.send(cmd);
        if (r === null) { fail = t("No reply — the relay may have dropped"); break; }
        if (r.startsWith("ERR")) { fail = r; break; }
      }
    } finally {
      this.busy = false;
      this.disabled(false);
    }
    if (fail) {
      this.setLog(fail, true);
      return;
    }
    this.setLog(t("Saved — relay is connecting…"), false);
    this.passIn.value = "";
    await new Promise((r) => setTimeout(r, 1500));
    await this.refresh();
  }

  private async doAp(enable: boolean) {
    const host = this.host;
    if (!host || this.busy) return;
    this.busy = true;
    this.disabled(true);
    try {
      this.report(await host.send(enable ? "NET AP" : "NET AP OFF"));
    } finally {
      this.busy = false;
      this.disabled(false);
    }
    await this.refresh();
  }

  private async doProto(which: "MONITOR" | "RH") {
    const host = this.host;
    if (!host || this.busy) return;
    this.busy = true;
    this.disabled(true);
    try {
      // The relay forwards the switch to the C5 head over UART. Firmware that
      // predates that only knows the HEAD prefix, so an ERR gets one retry.
      let r = await host.send(`PROTOCOL ${which}`);
      if (r?.startsWith("ERR")) r = await host.send(`HEAD PROTOCOL ${which}`);
      this.report(r);
    } finally {
      this.busy = false;
      this.disabled(false);
    }
    await this.refresh();
  }

  /** Surface a failed command in the log; a null reply means the link went quiet. */
  private report(reply: string | null) {
    if (reply === null) this.setLog(t("No reply — the relay may have dropped"), true);
    else if (reply.startsWith("ERR")) this.setLog(reply, true);
  }
}
