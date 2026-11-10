// Device discovery and management model.
// Aggregates three transport sources into a single list of manageable devices:
//   - USB serial   : serve.mjs auto-scans /dev/cu.usbmodem* (macOS), /dev/ttyACM* (Linux), etc.
//   - WiFi relay   : S3 relay boards that connected in via WS beacon (listed under kind:"remote")
//   - Ethernet(HTTP): RotorHazard or other scan heads reachable via HTTP server config (not yet surfaced)
// The list is polled from /api/endpoints and the caller subscribes to updates.
//
// NB: The existing serial/device.ts already exports a class named `Device`
// (the runtime USB-serial session with a board), so this module intentionally
// names its entity `ManagedDevice` to avoid a TS identifier collision at the
// main.ts import boundary.
// GPL-3.0-or-later.

export type DeviceKind = "serial" | "remote" | "uart";

export interface ManagedDevice {
  id: string;               // "serial:/dev/cu.usbmodem1301" or "node:widscan58-52a1d8"
  kind: DeviceKind;         // "serial" (USB), "remote" (WiFi relay), "uart" (Pi GPIO)
  label: string;            // human-readable label (tty name or relay id)
  path?: string;            // filesystem path for serial devices
  online: boolean;
  busy: boolean;            // already claimed by another page/session
  auto?: boolean;           // true = discovered by USB auto-scan, false = udev alias/manual
  scannable?: boolean;
  // ---- derived ----
  transport: "usb" | "wifi" | "ethernet";  // high-level transport family
}

/** Derive the high-level transport family from kind + id. */
function transportOf(d: { kind: DeviceKind; id: string }): ManagedDevice["transport"] {
  if (d.kind === "serial" || d.kind === "uart") return "usb";
  if (d.kind === "remote") {
    // node:* entries are S3 relay boards that connected over LAN/WiFi
    return "wifi";
  }
  return "ethernet";
}

export interface DeviceManagerEvents {
  (devices: ManagedDevice[]): void;
}

/**
 * Polls /api/endpoints every {@link pollMs} ms and emits a device list whenever
 * it changes (shallow compare of ids). Emits once immediately after the first
 * successful fetch, so callers get a known-good snapshot without further waits.
 */
export class DeviceManager {
  devices: ManagedDevice[] = [];
  private listeners = new Set<DeviceManagerEvents>();
  private timer: number | null = null;
  private pollMs: number;
  private controller: AbortController | null = null;

  constructor(pollMs = 2000) {
    this.pollMs = pollMs;
  }

  /** Start background polling. Safe to call multiple times. */
  start() {
    if (this.timer !== null) return;
    // immediate kickoff
    void this.fetchOnce();
    // Use the global form (not window.setInterval) so this file also
    // compiles and runs under Node (e.g. vitest run with environment:node).
    this.timer = setInterval(() => void this.fetchOnce(), this.pollMs);
  }

  stop() {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.controller?.abort();
    this.controller = null;
  }

  subscribe(fn: DeviceManagerEvents): () => void {
    this.listeners.add(fn);
    fn(this.devices); // fire immediately with current snapshot
    return () => this.listeners.delete(fn);
  }

  private async fetchOnce() {
    this.controller?.abort();
    // globalThis works in both browser and Node; AbortController is
    // available globally in modern Node (15+) and all evergreen browsers.
    const g: any = globalThis;
    const ac = new g.AbortController();
    this.controller = ac;
    try {
      const res = await g.fetch("/api/endpoints", { signal: ac.signal, cache: "no-store" });
      if (!res.ok) return;
      const json = (await res.json()) as {
        endpoints: Array<{
          id: string;
          kind: DeviceKind;
          label: string;
          path?: string;
          online?: boolean;
          busy?: boolean;
          auto?: boolean;
          scannable?: boolean;
        }>;
      };
      const next: ManagedDevice[] = json.endpoints.map((e) => ({
        id: e.id,
        kind: e.kind,
        label: e.label,
        path: e.path,
        online: e.online ?? true,
        busy: e.busy ?? false,
        auto: e.auto,
        scannable: e.scannable,
        transport: transportOf(e),
      }));
      if (!shallowEqualIds(this.devices, next)) {
        this.devices = next;
        for (const fn of this.listeners) fn(next);
      }
    } catch {
      /* offline or endpoint not reachable: keep last known list */
    }
  }
}

function shallowEqualIds(a: ManagedDevice[], b: ManagedDevice[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i].id !== b[i].id) return false;
  return true;
}

/** Pretty label for transport family (i18n-ready placeholders). */
export function transportLabel(t: ManagedDevice["transport"]): string {
  switch (t) {
    case "usb": return "USB";
    case "wifi": return "WiFi";
    case "ethernet": return "以太网";
  }
}
