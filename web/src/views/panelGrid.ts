// Pure 12-column geometry for the monitor workspace: snapping, clamping,
// collision push-down, docking zones and saved-layout validation.
// No DOM in here so the edge cases can be unit-tested. GPL-3.0-or-later.

export const COLS = 12;
export const ROW_H = 40; // row pitch in px, gap included
export const GAP = 12;
export const EDGE = 28; // pointer distance from a workspace edge that docks

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  collapsed?: boolean;
  /** Height in rows to restore when a collapsed panel expands again. */
  expandedH?: number;
}

export interface Size {
  w: number;
  h: number;
}

export interface Metrics {
  left: number; // workspace padding-left (position origin inside the box)
  top: number; // workspace padding-top
  innerWidth: number; // width available to the columns
  height: number; // height available for dropping (bottom-edge test)
  originX: number; // clientX of the content box's left edge (pointer tests)
  originY: number; // clientY of the content box's top edge
}

export type DockZone = "free" | "left" | "right" | "top" | "bottom";

export interface SavedLayout {
  v?: number;
  mode?: string;
  panels?: Record<string, unknown>;
}

export interface LayoutState {
  mode: "free" | "stack";
  panels: Record<string, Rect>;
}

const MAX_PASSES = 200;

/** Round to whole cells, keep the grid bounds and the panel's minimum size. */
export function clampRect<T extends Rect>(rect: T, cols = COLS, minW = 1, minH = 1): T {
  const w = Math.max(minW, Math.min(Math.round(rect.w), cols));
  const h = Math.max(minH, Math.round(rect.h));
  const x = Math.min(Math.max(0, Math.round(rect.x)), cols - w);
  const y = Math.max(0, Math.round(rect.y));
  return { ...rect, x, y, w, h };
}

export function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

export interface PanelRect extends Rect {
  id: string;
}

function firstOverlap(items: PanelRect[], movedId: string): PanelRect | null {
  for (let i = 0; i < items.length; i += 1) {
    for (let j = i + 1; j < items.length; j += 1) {
      if (!overlaps(items[i], items[j])) continue;
      // The panel being dragged never moves: it is what the user is pointing at.
      return items[i].id === movedId ? items[j] : items[i];
    }
  }
  return null;
}

/**
 * Place the moved panel and push whatever it covers downwards. `unresolved`
 * names panels that still overlap (the set does not fit at this column count).
 */
export function resolve(
  panels: PanelRect[],
  movedId: string,
  cols = COLS,
): { panels: PanelRect[]; unresolved: string[] } {
  const items = panels.map((p) => ({ ...p }));
  const moved = items.find((p) => p.id === movedId);
  if (moved) Object.assign(moved, clampRect(moved, cols));
  for (let pass = 0; pass < MAX_PASSES; pass += 1) {
    const pushed = firstOverlap(items, movedId);
    if (!pushed) return { panels: items, unresolved: [] };
    const anchor = items.find((p) => p !== pushed && overlaps(p, pushed));
    if (!anchor) return { panels: items, unresolved: [pushed.id] };
    pushed.y = anchor.y + anchor.h;
  }
  const stuck = new Set<string>();
  for (const a of items) {
    for (const b of items) {
      if (a !== b && overlaps(a, b)) {
        stuck.add(a.id);
        stuck.add(b.id);
      }
    }
  }
  return { panels: items, unresolved: [...stuck] };
}

export function bottomRow(rects: Rect[]): number {
  return rects.reduce((low, r) => Math.max(low, r.y + r.h), 0);
}

/** Pull every panel up as far as it can go without covering another. */
export function compact(panels: PanelRect[], cols = COLS): PanelRect[] {
  const placed: PanelRect[] = [];
  for (const p of [...panels].sort((a, b) => a.y - b.y || a.x - b.x)) {
    let y = p.y;
    while (y > 0) {
      const probe = { ...p, y: y - 1 };
      if (placed.some((q) => overlaps(q, probe))) break;
      y -= 1;
    }
    placed.push(clampRect({ ...p, y }, cols));
  }
  const byId = new Map(placed.map((p) => [p.id, p]));
  return panels.map((p) => byId.get(p.id)!);
}

/** Which dock target the pointer is over. The workspace edges win. */
export function dockZone(px: number, py: number, m: Metrics, edge = EDGE): DockZone {
  const relX = px - m.originX;
  const relY = py - m.originY;
  if (relY >= 0 && relY < edge) return "top";
  if (relY > m.height - edge) return "bottom";
  if (relX < edge) return "left";
  if (relX > m.innerWidth - edge) return "right";
  return "free";
}

/**
 * Drag result: `delta` is the pointer movement in cells. Left/right dock to a
 * half-width slot on the row under the pointer; top/bottom dock full width.
 */
export function moveRect(
  start: Rect,
  delta: { x: number; y: number },
  zone: DockZone,
  opts: { cols?: number; bottom: number; minW?: number; minH?: number },
): Rect {
  const cols = opts.cols ?? COLS;
  const minW = opts.minW ?? 1;
  const minH = opts.minH ?? 1;
  const half = Math.max(minW, Math.min(cols, Math.round(cols / 2)));
  const y = start.y + Math.round(delta.y);
  switch (zone) {
    case "top":
      return clampRect({ ...start, x: 0, y: 0, w: cols }, cols, minW, minH);
    case "bottom":
      return clampRect({ ...start, x: 0, y: opts.bottom, w: cols }, cols, minW, minH);
    case "left":
      return clampRect({ ...start, x: 0, y, w: half }, cols, minW, minH);
    case "right":
      return clampRect({ ...start, x: cols - half, y, w: half }, cols, minW, minH);
    default:
      return clampRect({ ...start, x: start.x + Math.round(delta.x), y, w: start.w }, cols, minW, minH);
  }
}

/** Bottom-right grip: `delta` is the pointer movement in cells. */
export function resizeRect(
  start: Rect,
  delta: { x: number; y: number },
  opts: { cols?: number; minW?: number; minH?: number } = {},
): Rect {
  const cols = opts.cols ?? COLS;
  return clampRect(
    { ...start, w: start.w + Math.round(delta.x), h: start.h + Math.round(delta.y) },
    cols,
    opts.minW ?? 1,
    opts.minH ?? 1,
  );
}

/** Cell rect -> pixels inside the workspace's content box. */
export function rectToPixels(rect: Rect, m: Metrics, cols = COLS): { left: number; top: number; width: number; height: number } {
  const pitch = (m.innerWidth + GAP) / cols;
  return {
    left: m.left + rect.x * pitch,
    top: m.top + rect.y * ROW_H,
    width: Math.max(1, rect.w * pitch - GAP),
    height: Math.max(1, rect.h * ROW_H - GAP),
  };
}

export const LAYOUT_VERSION = 1;

/**
 * Validate a stored layout against the panel defaults. Anything unusable
 * falls back per panel (never throws, never leaves a panel off-screen).
 */
export function readLayout(
  raw: unknown,
  defaults: Record<string, Rect>,
  mins: Record<string, Size>,
  defaultMode: "free" | "stack",
): LayoutState {
  const out: LayoutState = { mode: defaultMode, panels: {} };
  for (const [id, rect] of Object.entries(defaults)) {
    const min = mins[id] ?? { w: 1, h: 1 };
    out.panels[id] = clampRect(rect, COLS, min.w, min.h);
  }
  if (!raw || typeof raw !== "object") return out;
  const saved = raw as SavedLayout;
  if (saved.v !== LAYOUT_VERSION) return out;
  if (saved.mode === "free" || saved.mode === "stack") out.mode = saved.mode;
  if (!saved.panels || typeof saved.panels !== "object") return out;
  for (const [id, value] of Object.entries(saved.panels)) {
    if (!(id in out.panels) || !value || typeof value !== "object") continue;
    const v = value as Record<string, unknown>;
    if (![v.x, v.y, v.w, v.h].every((n) => typeof n === "number" && Number.isFinite(n))) continue;
    const min = mins[id] ?? { w: 1, h: 1 };
    const expandedH =
      typeof v.expandedH === "number" && Number.isFinite(v.expandedH) ? Math.max(min.h, Math.round(v.expandedH)) : undefined;
    const rect: Rect = { x: v.x as number, y: v.y as number, w: v.w as number, h: v.h as number };
    if (v.collapsed === true) rect.collapsed = true;
    if (expandedH !== undefined) rect.expandedH = expandedH;
    out.panels[id] = clampRect(rect, COLS, min.w, min.h);
  }
  return out;
}
