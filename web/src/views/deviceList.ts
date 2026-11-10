// Device list view — shows every USB/WiFi relay device the serve.mjs bridge can reach.
// Clicking a card opens a context menu: connect (scan), manage (config).
// GPL-3.0-or-later.

import { type ManagedDevice, DeviceManager, transportLabel } from "../model/devices";
import { el } from "../util/canvas";

export interface DeviceListActions {
  onConnect?: (device: ManagedDevice) => void;     // user chose this device for scanning
  onManage?: (device: ManagedDevice) => void;      // open relay config / rename dialog
  onFlash?: (device: ManagedDevice) => void;       // open firmware flash
  onClose?: () => void;                            // user dismissed the list
}

const KIND_ICON: Record<ManagedDevice["kind"], string> = {
  serial: "🔌",   // USB serial
  remote: "📶",   // WiFi relay node
  uart: "🖥️",     // Raspberry Pi GPIO UART
};

const TRANSPORT_ICON: Record<ManagedDevice["transport"], string> = {
  usb: "🔌",
  wifi: "📶",
  ethernet: "🌐",
};

export class DeviceList {
  private manager: DeviceManager;
  private actions: DeviceListActions;
  private root: HTMLDialogElement;
  private grid: HTMLDivElement;
  private title: HTMLHeadingElement;
  private countLabel: HTMLSpanElement;
  private unsubscribe: (() => void) | null = null;

  constructor(manager: DeviceManager, actions: DeviceListActions = {}) {
    this.manager = manager;
    this.actions = actions;

    this.root = el("dialog", { class: "device-list", role: "dialog", "aria-modal": "true" }) as HTMLDialogElement;
    this.title = el("h2", { class: "device-list__title" }, "设备管理");
    this.countLabel = el("span", { class: "device-list__count" });
    this.grid = el("div", { class: "device-list__grid" });
    const header = el("div", { class: "device-list__header" }, this.title, this.countLabel);
    const closeBtn = el("button", { class: "device-list__close", title: "关闭" }, "✕");
    closeBtn.addEventListener("click", () => { this.root.close(); this.actions.onClose?.(); });
    header.append(closeBtn);
    this.root.append(header, this.grid);

    this.unsubscribe = manager.subscribe((devices) => this.render(devices));
  }

  element(): HTMLDialogElement { return this.root; }

  open() {
    this.root.showModal();
    this.render(this.manager.devices);
  }

  close() { this.root.close(); }

  destroy() {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.root.remove();
  }

  private render(devices: ManagedDevice[]) {
    this.countLabel.textContent = devices.length === 0
      ? "— 正在扫描..."
      : `${devices.length} 个设备可连接`;
    // Group by transport: WiFi first (most common relay), then USB, then Ethernet
    const order: ManagedDevice["transport"][] = ["wifi", "usb", "ethernet"];
    const grouped = new Map<ManagedDevice["transport"], ManagedDevice[]>();
    for (const t of order) grouped.set(t, []);
    for (const d of devices) {
      const bucket = grouped.get(d.transport)!;
      bucket.push(d);
    }
    this.grid.innerHTML = "";
    let any = false;
    for (const t of order) {
      const list = grouped.get(t)!;
      if (list.length === 0) continue;
      any = true;
      const section = el("section", { class: "device-section" });
      const h = el("h3", { class: "device-section__title" }, `${TRANSPORT_ICON[t]} ${transportLabel(t)} (${list.length})`);
      section.append(h);
      const cards = el("div", { class: "device-section__cards" });
      for (const d of list) cards.append(this.renderCard(d));
      section.append(cards);
      this.grid.append(section);
    }
    if (!any) {
      this.grid.append(el("p", { class: "device-list__empty" },
        "未发现任何可管理设备。请检查：\n  • USB 设备已插上？\n  • WiFi relay 板子已通电并连上服务器？\n  • serve.mjs 在运行？"));
    }
  }

  private renderCard(d: ManagedDevice): HTMLDivElement {
    const card = el("div", {
      class: "device-card" +
        (d.online ? " device-card--online" : " device-card--offline") +
        (d.busy ? " device-card--busy" : ""),
      "data-device-id": d.id,
    });

    // Status dot
    const status = el("span", {
      class: "device-card__status " + (d.online ? (d.busy ? "busy" : "online") : "offline"),
      title: d.online ? (d.busy ? "已被占用" : "在线") : "离线",
    });

    // Icon
    const icon = el("span", { class: "device-card__icon" }, KIND_ICON[d.kind]);

    // Main column
    const main = el("div", { class: "device-card__main" });
    const nameLine = el("div", { class: "device-card__name-line" });
    const name = el("span", { class: "device-card__name" }, d.label);
    const kindTag = el("span", { class: "device-card__kind" },
      d.kind === "remote" ? "Relay" : d.kind === "serial" ? "Scanner" : "UART");
    nameLine.append(name, kindTag);
    const subline = el("div", { class: "device-card__subline" });
    if (d.kind === "serial" && d.path) subline.textContent = d.path;
    else if (d.kind === "remote") subline.textContent = d.auto ? "自动发现" : "WiFi Beacon";
    else subline.textContent = d.id;
    main.append(nameLine, subline);

    // Actions
    const actionBar = el("div", { class: "device-card__actions" });
    const connectBtn = el("button", {
      class: "device-card__btn device-card__btn--connect primary" + (!d.online || d.busy ? " disabled" : ""),
      title: !d.online ? "设备离线" : d.busy ? "已被占用" : "连接此设备开始扫描",
    }, "连接") as HTMLButtonElement;
    connectBtn.disabled = !d.online || d.busy;
    connectBtn.addEventListener("click", () => this.actions.onConnect?.(d));
    const manageBtn = el("button", {
      class: "device-card__btn device-card__btn--manage" + (d.kind !== "remote" ? " disabled" : ""),
      title: d.kind === "remote" ? "配网、改名、切换模式" : "仅 WiFi relay 支持管理面板",
    }, "管理") as HTMLButtonElement;
    manageBtn.disabled = d.kind !== "remote";
    manageBtn.addEventListener("click", () => this.actions.onManage?.(d));
    actionBar.append(connectBtn, manageBtn);

    card.append(status, icon, main, actionBar);
    return card;
  }
}
