// Unit tests for model/devices.ts — DeviceManager polling + subscription
// and the pure helpers (transportOf, transportLabel).
//
// Runs under vitest's default node environment; DeviceManager uses globalThis.*
// so it works in both Node and browser. Each test is fully isolated via
// vi.stubGlobal("fetch", ...) + vi.unstubAllGlobals() in afterEach.
//
// Gotchas (vitest 5 specific):
//   1. vi.waitFor waits by polling predicate, but fetchOnce() chains two
//      awaits (fetch + res.json) that need multiple microtask drains plus
//      an event-loop turn to actually run. `await Promise.resolve()` once
//      or even several times is NOT enough — the vitest scheduler keeps
//      the async test's own microtask queue ahead of the fire-and-forget
//      fetchOnce()'s continuation.
//   2. So we always wait with a *small real wall-clock delay*
//      (setTimeout 10–100 ms) after start() before asserting. This gives
//      both the initial kickoff fetch and the first timer-poll fetch time
//      to fully resolve, regardless of scheduler internals.
//   3. For multi-poll tests we use vi.waitFor on fetchImpl.mock.calls.length
//      (a *synchronous* signal that fetch was invoked) followed by the same
//      small setTimeout drain — that pattern is reliable in vitest 5.
// GPL-3.0-or-later.

import { afterEach, describe, expect, it, vi } from "vitest";
import { DeviceManager, transportLabel, type ManagedDevice } from "./devices";

// ---- helpers -------------------------------------------------------------

function fakeEndpoint(overrides: Partial<ManagedDevice & { id: string; kind: ManagedDevice["kind"] }>): any {
  return {
    id: "serial:/dev/cu.fake",
    kind: "serial" as const,
    label: "fake",
    online: true,
    busy: false,
    ...overrides,
  };
}

/** Stub globalThis.fetch with the given impl and return a DeviceManager. */
function buildManager(fetchImpl: (...args: any[]) => any, pollMs = 200) {
  vi.stubGlobal("fetch", fetchImpl);
  return new DeviceManager(pollMs);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Let all fire-and-forget async chains in DeviceManager run to completion.
 *  10 ms of real time is plenty for fetch() → Response.json() → emit. */
const drain = (ms = 50) => new Promise<void>((r) => setTimeout(r, ms));

// =========================================================================
// transportLabel — pure function, no DOM, no IO
// =========================================================================
describe("transportLabel", () => {
  it("labels usb", () => expect(transportLabel("usb")).toBe("USB"));
  it("labels wifi", () => expect(transportLabel("wifi")).toBe("WiFi"));
  it("labels ethernet", () => expect(transportLabel("ethernet")).toBe("以太网"));
});

// =========================================================================
// DeviceManager — poll / subscribe lifecycle (real timers)
// =========================================================================
describe("DeviceManager", () => {
  it("starts polling and emits immediately after first fetch", async () => {
    const endpoints = [
      fakeEndpoint({ id: "node:widscan58-abc", kind: "remote", label: "widscan58-abc" }),
    ];
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ endpoints }), { status: 200 }),
    );
    const mgr = buildManager(fetchImpl);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();

    // Wait for fetch() to be called, then drain real time so fetchOnce's
    // fetch → res.json → emit chain fully resolves.
    await vi.waitFor(() => fetchImpl.mock.calls.length >= 1, { timeout: 2000 });
    await drain();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(snapshots.length).toBe(2);            // subscribe [] + fetch emit
    expect(snapshots[0].length).toBe(0);         // subscribe immediate fire
    const last = snapshots[snapshots.length - 1];
    expect(last.length).toBe(1);
    expect(last[0].id).toBe("node:widscan58-abc");
    expect(last[0].transport).toBe("wifi");
    mgr.stop();
  });

  it("emits again when the device set changes across polls", async () => {
    const endpointsV1 = [
      fakeEndpoint({ id: "serial:/dev/one", kind: "serial", label: "cu.usbmodem1111" }),
    ];
    const endpointsV2 = [
      fakeEndpoint({ id: "serial:/dev/one", kind: "serial", label: "cu.usbmodem1111" }),
      fakeEndpoint({ id: "node:widscan58-xyz", kind: "remote", label: "widscan58-xyz" }),
    ];
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call++;
      const body = JSON.stringify({ endpoints: call === 1 ? endpointsV1 : endpointsV2 });
      return new Response(body, { status: 200 });
    });
    const mgr = buildManager(fetchImpl, 10);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();

    await vi.waitFor(() => fetchImpl.mock.calls.length >= 2, { timeout: 5000 });
    await drain();
    mgr.stop();

    // subscribe empty + V1 emit + V2 emit
    expect(snapshots.length).toBeGreaterThanOrEqual(3);
    const last = snapshots[snapshots.length - 1];
    expect(last).toHaveLength(2);
  });

  it("does NOT emit when the id set is unchanged across polls", async () => {
    const endpoints = [fakeEndpoint({ id: "node:only-one", kind: "remote" })];
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ endpoints }), { status: 200 }));
    const mgr = buildManager(fetchImpl, 10);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();

    // Let poll() fire 3 times (initial kickoff + 2 timer polls)
    await vi.waitFor(() => fetchImpl.mock.calls.length >= 3, { timeout: 5000 });
    await drain();
    mgr.stop();

    // subscribe sync fire ([]) + exactly one real fetch emit = 2
    // Polls 2 and 3 return identical ids → shallowEqualIds filters → no emit
    expect(snapshots.length).toBe(2);
    expect(snapshots[0].length).toBe(0);
    expect(snapshots[1].length).toBe(1);
  });

  it("survives fetch failure — keeps last known list", async () => {
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call++;
      if (call === 1) {
        return new Response(
          JSON.stringify({ endpoints: [fakeEndpoint({ id: "node:present", kind: "remote" })] }),
          { status: 200 },
        );
      }
      throw new Error("offline");
    });
    const mgr = buildManager(fetchImpl, 10);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();

    await vi.waitFor(() => fetchImpl.mock.calls.length >= 2, { timeout: 3000 });
    await drain();
    mgr.stop();

    // Last snapshot = the successful first fetch result (second fetch threw)
    const last = snapshots[snapshots.length - 1];
    expect(last.length).toBe(1);
    expect(last[0].id).toBe("node:present");
  });

  it("survives non-OK HTTP status — no spurious emit", async () => {
    const fetchImpl = vi.fn(async () => new Response("nope", { status: 500 }));
    const mgr = buildManager(fetchImpl, 10);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();

    await vi.waitFor(() => fetchImpl.mock.calls.length >= 2, { timeout: 5000 });
    await drain();
    mgr.stop();

    // subscribe immediate fire only — server never returns usable data
    expect(snapshots.length).toBe(1);
    expect(snapshots[0].length).toBe(0);
  });

  it("stop() halts polling — no more fetches after stop", async () => {
    const endpoints = [fakeEndpoint({ id: "node:x", kind: "remote" })];
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ endpoints }), { status: 200 }));
    const mgr = buildManager(fetchImpl, 10);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();
    await vi.waitFor(() => fetchImpl.mock.calls.length >= 1, { timeout: 2000 });
    await drain();
    mgr.stop();
    const countAfterStop = fetchImpl.mock.calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect(fetchImpl.mock.calls.length).toBe(countAfterStop);
  });

  it("transport derivation: serial→usb, remote→wifi, uart→usb", async () => {
    const endpoints = [
      fakeEndpoint({ id: "serial:/dev/cu.usbmodem1301", kind: "serial", label: "cu.usbmodem1301" }),
      fakeEndpoint({ id: "node:widscan58-abc", kind: "remote", label: "widscan58-abc" }),
      fakeEndpoint({ id: "uart:/dev/serial0", kind: "uart", label: "serial0" }),
    ];
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ endpoints }), { status: 200 }));
    const mgr = buildManager(fetchImpl);
    const snapshots: ManagedDevice[][] = [];
    mgr.subscribe((list) => snapshots.push(list));
    mgr.start();
    await vi.waitFor(() => fetchImpl.mock.calls.length >= 1, { timeout: 3000 });
    await drain();
    mgr.stop();

    const last = snapshots[snapshots.length - 1];
    expect(last.length).toBe(3);
    const byKind = new Map(last.map((d) => [d.kind, d.transport]));
    expect(byKind.get("serial")).toBe("usb");
    expect(byKind.get("remote")).toBe("wifi");
    expect(byKind.get("uart")).toBe("usb");
  });
});
