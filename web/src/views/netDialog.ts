// "Device management" dialog: WiFi relay config, AP control, C5 mode switch.
// GPL-3.0-or-later.
import { el } from "../util/canvas";
import { t } from "../i18n";

export interface NetStatus {
  sta: string;     // "up" | "down"
  ip?: string;
  ssid?: string;
  rssi?: number;
  ws: string;      // "up" | "down" | "dial"
  url?: string;
  ap?: string;     // "up" | "down"
  last?: string;
}

export interface DeviceMgmtHost {
  /** Send a raw command line, return its reply (the "OK"/"ERR" or the leading-text line). */
  send(cmd: string): Promise<string | null>;
  /** Whether this dialog actually has a device to talk to. */
  connected(): boolean;
}

/** Normalize a value that may be a boolean or "up"/"down" string. */
function isUp(v: unknown): boolean {
  return v === true || v === "up" || v === 1;
}
function isDown(v: unknown): boolean {
  return v === false || v === "down" || v === 0;
}

/** Try parse a NET STATUS reply. Returns null if the reply isn't parseable. */
export function parseNetStatus(reply: string | null): NetStatus | null {
  if (!reply) return null;
  // Format: "NET {...}" or "NET <json>" — we look for a JSON object after "NET ".
  const idx = reply.indexOf("{");
  if (idx < 0) return null;
  try {
    const j = JSON.parse(reply.slice(idx));
    const staUp = isUp(j.sta);
    const wsUp = isUp(j.ws);
    const wsDown = isDown(j.ws);
    const out: NetStatus = {
      sta: staUp ? "up" : "down",
      ws: wsUp ? "up" : wsDown ? "down" : "dial",
      url: j.url,
      last: j.sta_reason ?? j.last,
    };
    if (staUp) { out.ip = j.ip ?? j.ip4; out.ssid = j.ssid; out.rssi = j.rssi; }
    if (isUp(j.ap)) out.ap = "up";
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

  // --- NET status card ---
  private staChip = el("span", { class: "chip" }, "—");
  private ipRow = el("div", { class: "kv" });
  private wsChip = el("span", { class: "chip" }, "—");
  private urlRow = el("div", { class: "kv" });
  private apChip = el("span", { class: "chip" }, "—");
  private lastRow = el("div", { class: "kv" });

  // --- WiFi settings form ---
  private ssidSel = el("select", { class: "net-ssid-sel" }) as HTMLSelectElement;
  private ssidMan = el("input", { class: "net-ssid-man", placeholder: t("Or type manually") }) as HTMLInputElement;
  private passIn = el("input", { type: "password", class: "net-pass", placeholder: t("Wi-Fi password") }) as HTMLInputElement;
  private urlIn = el("input", { class: "net-url", placeholder: t("ws://<host>:8080/node?id=<relay-id>") }) as HTMLInputElement;
  private idIn = el("input", { class: "net-id", placeholder: t("relay id (optional)") }) as HTMLInputElement;
  private saveLog = el("div", { class: "net-save-log" });

  // --- Actions ---
  private scanBtn = el("button", {}, t("Scan APs"));
  private saveBtn = el("button", { class: "primary" }, t("Save & Connect"));
  private apBtn = el("button", {}, t("Start AP mode"));
  private stopApBtn = el("button", {}, t("Stop AP mode"));
  private statusBtn = el("button", {}, t("Refresh status"));

  // --- C5 mode ---
  private protoChip = el("span", { class: "chip" }, "—");
  private monBtn = el("button", {}, t("PROTOCOL MONITOR"));
  private rhBtn = el("button", {}, t("PROTOCOL RH"));

  constructor() {
    this.el = el(
      "dialog",
      { class: "net-dialog" },
      el("form", { method: "dialog" },
        el("h2", {}, t("Device management")),
        // Section: NET status
        el("section", { class: "net-section" },
          el("h3", {}, t("Network status")),
          el("div", { class: "net-status-grid" },
            el("div", { class: "kv" }, el("span", {}, t("STA")), this.staChip),
            this.ipRow,
            el("div", { class: "kv" }, el("span", {}, t("Bridge")), this.wsChip),
            this.urlRow,
            el("div", { class: "kv" }, el("span", {}, t("SoftAP")), this.apChip),
            this.lastRow,
          ),
          this.statusBtn,
        ),
        // Section: WiFi settings
        el("section", { class: "net-section" },
          el("h3", {}, t("Wi-Fi settings")),
          el("label", {}, t("SSID"), this.ssidSel),
          this.ssidMan,
          el("label", {}, t("Password"), this.passIn),
          el("label", {}, t("Server URL")),
          this.urlIn,
          el("label", {}, t("Relay ID (optional)")),
          this.idIn,
          el("div", { class: "net-actions" }, this.scanBtn, this.saveBtn),
          this.saveLog,
        ),
        // Section: AP fallback
        el("section", { class: "net-section" },
          el("h3", {}, t("SoftAP fallback")),
          el("p", { class: "hint" }, t("If the relay can't join Wi-Fi for 3 minutes it brings up a softAP with the SSID Widscan58-<chip>. Connect to it and open http://192.168.4.1 to configure. You can also force it here.")),
          el("div", { class: "net-actions" }, this.apBtn, this.stopApBtn),
        ),
        // Section: C5 mode
        el("section", { class: "net-section" },
          el("h3", {}, t("C5 scanner mode")),
          el("div", { class: "kv" }, el("span", {}, t("Current")), this.protoChip),
          el("div", { class: "net-actions" }, this.monBtn, this.rhBtn),
          el("p", { class: "hint" }, t("MONITOR = spectrum scanning (this dashboard). RH = RotorHazard lap timer.")),
        ),
        el("div", { class: "dialog-footer" },
          el("button", { value: "close" }, t("Close")),
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
    this.el.addEventListener("close", () => { this.host = null; });
  }

  async open(host: DeviceMgmtHost) {
    this.host = host;
    this.el.showModal();
    this.reset();
    await this.refresh();
  }

  private reset() {
    this.ssidSel.replaceChildren(new Option(t("(none — click Scan)"), ""));
    this.ssidMan.value = "";
    this.passIn.value = "";
    this.urlIn.value = "";
    this.idIn.value = "";
    this.saveLog.textContent = "";
    this.setChip(this.staChip, "down", t("down"));
    this.setChip(this.wsChip, "down", t("down"));
    this.setChip(this.apChip, "down", t("down"));
    this.setChip(this.protoChip, "?", "—");
    this.ipRow.innerHTML = "";
    this.urlRow.innerHTML = "";
    this.lastRow.innerHTML = "";
  }

  private setChip(chip: HTMLElement, state: string, text: string) {
    chip.textContent = text;
    chip.classList.remove("up", "down", "dial", "q");
    chip.classList.add(state === "?" ? "q" : state);
  }

  private disabled(v: boolean) {
    [this.scanBtn, this.saveBtn, this.apBtn, this.stopApBtn, this.statusBtn, this.monBtn, this.rhBtn].forEach((b) => (b as HTMLButtonElement).disabled = v);
  }

  private async refresh() {
    if (!this.host || !this.host.connected()) return;
    this.disabled(true);
    try {
      const [netReply, protoReply, infoReply] = await Promise.all([
        this.host.send("NET STATUS"),
        this.host.send("PROTOCOL?"),
        this.host.send("INFO"),
      ]);
      const s = parseNetStatus(netReply);
      if (s) {
        this.setChip(this.staChip, s.sta, s.sta === "up" ? t("connected") : t("down"));
        if (s.ip) this.ipRow.textContent = `${t("IP")}: ${s.ip}${s.ssid ? ` · ${s.ssid}${s.rssi !== undefined ? ` (${s.rssi}dBm)` : ""}` : ""}`;
        else this.ipRow.textContent = "";
        this.setChip(this.wsChip, s.ws, s.ws === "up" ? t("bridge up") : s.ws === "dial" ? t("connecting") : t("down"));
        if (s.url) this.urlRow.textContent = `${t("URL")}: ${s.url}`; else this.urlRow.textContent = "";
        if (s.ap) this.setChip(this.apChip, s.ap, t("AP open"));
        else this.setChip(this.apChip, "down", t("closed"));
        if (s.last) this.lastRow.textContent = `${t("Last")}: ${s.last}`; else this.lastRow.textContent = "";
        if (!this.urlIn.value && s.url) this.urlIn.value = s.url;
      }
      if (protoReply) {
        const p = protoReply.split(" ").pop()?.trim() ?? "";
        this.setChip(this.protoChip, "?", p.toUpperCase());
      }
      if (infoReply) {
        // INFO widscan58|esp32s3|... — S3 relay node, not C5. Protocol command goes through relay's HEAD prefix only when we want C5 mode.
        // NET STATUS tells us whether AP is open etc. Enough for this panel.
      }
    } finally { this.disabled(false); }
  }

  private async doScan() {
    if (!this.host) return;
    this.scanBtn.disabled = true;
    this.scanBtn.textContent = t("Scanning…");
    const reply = await this.host.send("NET SCAN");
    const aps = parseNetScan(reply);
    this.ssidSel.replaceChildren();
    if (aps.length === 0) {
      this.ssidSel.append(new Option(t("(no APs found — check Wi-Fi is on)"), ""));
    } else {
      // Deduplicate while preserving scan order
      const seen = new Set<string>();
      for (const a of aps) {
        if (seen.has(a)) continue;
        seen.add(a);
        this.ssidSel.append(new Option(a, a));
      }
    }
    this.scanBtn.disabled = false;
    this.scanBtn.textContent = t("Scan APs");
  }

  private async doSave() {
    if (!this.host) return;
    const ssid = this.ssidMan.value.trim() || this.ssidSel.value;
    const pass = this.passIn.value;
    const url = this.urlIn.value.trim();
    const id = this.idIn.value.trim();
    if (!ssid || !url) {
      this.saveLog.textContent = t("SSID and URL are required");
      this.saveLog.classList.add("err");
      return;
    }
    this.disabled(true);
    this.saveLog.classList.remove("err");
    this.saveLog.textContent = t("Saving…");
    const lines: string[] = [];
    lines.push(`NET SSID ${ssid}`);
    if (pass) lines.push(`NET PASS ${pass}`);
    lines.push(`NET URL ${url}`);
    if (id) lines.push(`NET ID ${id}`);
    lines.push("NET SAVE");
    lines.push("NET ON");
    let allOk = true;
    for (const cmd of lines) {
      const r = await this.host.send(cmd);
      if (r?.startsWith("ERR")) { allOk = false; break; }
    }
    this.saveLog.textContent = allOk ? t("Saved — relay is connecting…") : t("Failed (check command log)");
    await new Promise((r) => setTimeout(r, 1500));
    await this.refresh();
    this.disabled(false);
  }

  private async doAp(enable: boolean) {
    if (!this.host) return;
    const reply = await this.host.send(enable ? "NET AP" : "NET AP OFF");
    if (reply?.startsWith("ERR")) {
      this.saveLog.textContent = reply;
      this.saveLog.classList.add("err");
    }
    await this.refresh();
  }

  private async doProto(which: "MONITOR" | "RH") {
    if (!this.host) return;
    // For S3 relay: PROTOCOL MONITOR/RH goes through the relay to the C5 head via HEAD prefix.
    // But the S3 commands.c PROTOCOL branch with RX_WIFI_RELAY handles it directly now too
    // (we added the forwarding). Try raw first; if the reply is "ERR noscanner" we know
    // we're talking to an S3 that isn't patched. In that case fall back to HEAD prefix.
    let r = await this.host.send(`PROTOCOL ${which}`);
    if (r?.startsWith("ERR")) r = await this.host.send(`HEAD PROTOCOL ${which}`);
    await this.refresh();
  }
}
