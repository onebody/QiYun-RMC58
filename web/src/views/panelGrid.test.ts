// Workspace grid geometry: snapping, push-down, compaction, docking and the
// saved-layout validator. GPL-3.0-or-later.
import { describe, expect, it } from "vitest";
import {
  LAYOUT_VERSION,
  bottomRow,
  clampRect,
  compact,
  dockZone,
  moveRect,
  overlaps,
  readLayout,
  rectToPixels,
  resizeRect,
  resolve,
  type Metrics,
  type PanelRect,
} from "./panelGrid";

const panel = (id: string, x: number, y: number, w: number, h: number): PanelRect => ({ id, x, y, w, h });
const metrics: Metrics = { left: 16, top: 12, innerWidth: 1164, height: 800, originX: 0, originY: 0 };

describe("clampRect", () => {
  it("rounds to whole cells and keeps the minimum size", () => {
    expect(clampRect({ x: 1.4, y: 2.6, w: 3.2, h: 4.7 })).toEqual({ x: 1, y: 3, w: 3, h: 5 });
    expect(clampRect({ x: 0, y: 0, w: 1, h: 1 }, 12, 4, 5)).toMatchObject({ w: 4, h: 5 });
  });

  it("keeps the panel inside the grid", () => {
    expect(clampRect({ x: 9, y: -3, w: 6, h: 4 })).toMatchObject({ x: 6, y: 0 });
    expect(clampRect({ x: 0, y: 0, w: 20, h: 4 })).toMatchObject({ x: 0, w: 12 });
  });

  it("preserves extra fields (collapsed, expandedH)", () => {
    expect(clampRect({ x: 0, y: 0, w: 2, h: 2, collapsed: true, expandedH: 7 })).toMatchObject({
      collapsed: true,
      expandedH: 7,
    });
  });
});

describe("overlaps", () => {
  it("treats touching edges as separate", () => {
    expect(overlaps({ x: 0, y: 0, w: 6, h: 4 }, { x: 6, y: 0, w: 6, h: 4 })).toBe(false);
    expect(overlaps({ x: 0, y: 0, w: 6, h: 4 }, { x: 0, y: 4, w: 6, h: 4 })).toBe(false);
    expect(overlaps({ x: 0, y: 0, w: 6, h: 4 }, { x: 5, y: 3, w: 6, h: 4 })).toBe(true);
  });
});

describe("resolve", () => {
  it("keeps the dragged panel and pushes what it covers down", () => {
    const out = resolve([panel("a", 0, 0, 6, 6), panel("b", 0, 3, 6, 6)], "b");
    expect(out.unresolved).toEqual([]);
    expect(out.panels.find((p) => p.id === "b")).toMatchObject({ y: 3 });
    expect(out.panels.find((p) => p.id === "a")).toMatchObject({ y: 9 });
  });

  it("stacks the column below the panel the user dropped", () => {
    const out = resolve(
      [panel("a", 0, 0, 12, 4), panel("b", 0, 4, 12, 4), panel("c", 0, 2, 12, 4)],
      "c",
    );
    expect(out.unresolved).toEqual([]);
    const at = (id: string) => out.panels.find((p) => p.id === id)!.y;
    expect(at("c")).toBe(2);
    expect(at("b")).toBe(6);
    expect(at("a")).toBe(10);
  });

  it("clamps the moved panel into the grid", () => {
    const out = resolve([panel("a", 9, 0, 6, 4)], "a");
    expect(out.panels[0]).toMatchObject({ x: 6, w: 6 });
  });
});

describe("bottomRow", () => {
  it("returns the lowest occupied row", () => {
    expect(bottomRow([{ x: 0, y: 0, w: 6, h: 4 }, { x: 6, y: 2, w: 6, h: 9 }])).toBe(11);
    expect(bottomRow([])).toBe(0);
  });
});

describe("compact", () => {
  it("pulls panels up over gaps without changing their columns", () => {
    const out = compact([panel("a", 0, 0, 12, 3), panel("b", 0, 8, 12, 3)]);
    expect(out.find((p) => p.id === "b")).toMatchObject({ y: 3, x: 0, w: 12 });
  });

  it("stops beside a taller neighbour", () => {
    const out = compact([panel("a", 0, 0, 6, 6), panel("b", 6, 0, 6, 3), panel("c", 0, 8, 6, 3)]);
    expect(out.find((p) => p.id === "c")).toMatchObject({ y: 6 });
  });

  it("does not move a panel that is already flush", () => {
    const input = [panel("a", 0, 0, 6, 4), panel("b", 6, 0, 6, 4)];
    expect(compact(input).map((p) => p.y)).toEqual([0, 0]);
  });
});

describe("dockZone", () => {
  it("maps the edges and the free middle", () => {
    expect(dockZone(600, 5, metrics)).toBe("top");
    expect(dockZone(600, 790, metrics)).toBe("bottom");
    expect(dockZone(20, 400, metrics)).toBe("left");
    expect(dockZone(1180, 400, metrics)).toBe("right");
    expect(dockZone(600, 400, metrics)).toBe("free");
  });

  it("only docks near the outer edges", () => {
    expect(dockZone(52, 400, metrics)).toBe("free");
    expect(dockZone(27, 400, metrics)).toBe("left");
    expect(dockZone(1137, 400, metrics)).toBe("right");
  });

  it("uses the workspace origin, not the viewport", () => {
    const m = { ...metrics, originX: 300, originY: 500 };
    expect(dockZone(310, 520, m)).toBe("top");
    expect(dockZone(310, 1290, m)).toBe("bottom");
    expect(dockZone(310, 900, m)).toBe("left");
    expect(dockZone(900, 900, m)).toBe("free");
  });
});

describe("moveRect", () => {
  const opts = { bottom: 30, minW: 3, minH: 2 };
  const start = { x: 3, y: 5, w: 6, h: 7 };

  it("free drag moves by whole cells", () => {
    expect(moveRect(start, { x: 2.4, y: -1.6 }, "free", opts)).toMatchObject({ x: 5, y: 3, w: 6, h: 7 });
  });

  it("top and bottom dock full width", () => {
    expect(moveRect(start, { x: 99, y: 99 }, "top", opts)).toMatchObject({ x: 0, y: 0, w: 12, h: 7 });
    expect(moveRect(start, { x: 0, y: 0 }, "bottom", opts)).toMatchObject({ x: 0, y: 30, w: 12 });
  });

  it("left and right dock to half-width slots", () => {
    expect(moveRect(start, { x: 0, y: 2 }, "left", opts)).toMatchObject({ x: 0, y: 7, w: 6 });
    expect(moveRect(start, { x: 0, y: 2 }, "right", opts)).toMatchObject({ x: 6, y: 7, w: 6 });
  });

  it("respects the panel's minimum width on a half slot", () => {
    expect(moveRect(start, { x: 0, y: 0 }, "left", { ...opts, minW: 8 })).toMatchObject({ x: 0, w: 8 });
  });
});

describe("resizeRect", () => {
  it("grows and shrinks from the bottom-right corner", () => {
    const start = { x: 2, y: 3, w: 5, h: 6 };
    expect(resizeRect(start, { x: 2.2, y: -1.3 }, { minW: 3, minH: 4 })).toMatchObject({ x: 2, y: 3, w: 7, h: 5 });
    expect(resizeRect(start, { x: -99, y: -99 }, { minW: 3, minH: 4 })).toMatchObject({ w: 3, h: 4 });
  });

  it("cannot grow past the last column", () => {
    expect(resizeRect({ x: 8, y: 0, w: 4, h: 4 }, { x: 40, y: 0 })).toMatchObject({ x: 0, w: 12 });
  });
});

describe("rectToPixels", () => {
  it("uses the column pitch and row height", () => {
    // (1164 + 12) / 12 = 98 px per column; rows are 40 px.
    expect(rectToPixels({ x: 0, y: 0, w: 6, h: 3 }, metrics)).toEqual({ left: 16, top: 12, width: 576, height: 108 });
    expect(rectToPixels({ x: 6, y: 2, w: 6, h: 1 }, metrics)).toEqual({ left: 604, top: 92, width: 576, height: 28 });
  });
});

describe("readLayout", () => {
  const defaults = {
    a: { x: 0, y: 0, w: 12, h: 6 },
    b: { x: 0, y: 6, w: 6, h: 6 },
  };
  const mins = { a: { w: 4, h: 4 }, b: { w: 3, h: 3 } };

  it("returns the defaults without a saved layout", () => {
    expect(readLayout(null, defaults, mins, "free")).toEqual({ mode: "free", panels: defaults });
    expect(readLayout(undefined, defaults, mins, "stack").mode).toBe("stack");
  });

  it("restores a saved layout of the current version", () => {
    const saved = {
      v: LAYOUT_VERSION,
      mode: "stack",
      panels: {
        a: { x: 0, y: 2, w: 12, h: 5 },
        b: { x: 6, y: 7, w: 6, h: 4, collapsed: true, expandedH: 8 },
      },
    };
    expect(readLayout(saved, defaults, mins, "free")).toEqual({
      mode: "stack",
      panels: {
        a: { x: 0, y: 2, w: 12, h: 5 },
        b: { x: 6, y: 7, w: 6, h: 4, collapsed: true, expandedH: 8 },
      },
    });
  });

  it("ignores a layout from another version or a junk document", () => {
    for (const raw of [{ v: 0, panels: { a: { x: 0, y: 0, w: 1, h: 1 } } }, 42, "x", {}]) {
      expect(readLayout(raw, defaults, mins, "free")).toEqual({ mode: "free", panels: defaults });
    }
  });

  it("falls back per panel on bad fields and clamps the rest", () => {
    const saved = {
      v: LAYOUT_VERSION,
      mode: "free",
      panels: {
        a: { x: "nope", y: 0, w: 12, h: 6 },
        b: { x: 9, y: -4, w: 1, h: 1 },
        ghost: { x: 0, y: 0, w: 4, h: 4 },
      },
    };
    const out = readLayout(saved, defaults, mins, "free");
    expect(out.panels.a).toEqual(defaults.a);
    expect(out.panels.b).toMatchObject({ x: 9, y: 0, w: 3, h: 3 });
    expect("ghost" in out.panels).toBe(false);
  });
});
