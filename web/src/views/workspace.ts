// Monitor workspace: wraps each card as a panel that can be collapsed, dragged,
// resized and docked on a 12-column grid. GPL-3.0-or-later.
import { t } from "../i18n";
import { el } from "../util/canvas";
import {
  COLS,
  GAP,
  LAYOUT_VERSION,
  ROW_H,
  bottomRow,
  clampRect,
  compact,
  dockZone,
  moveRect,
  readLayout,
  rectToPixels,
  resizeRect,
  resolve,
  type LayoutState,
  type Metrics,
  type PanelRect,
  type Rect,
  type Size,
} from "./panelGrid";

export type LayoutMode = "free" | "stack";

export interface PanelSpec {
  id: string;
  /** The view's card; its first <h2> becomes the drag handle. */
  view: HTMLElement;
  minW?: number;
  minH?: number;
}

export interface WorkspaceOptions {
  storageKey: string;
  specs: PanelSpec[];
  defaults: Record<string, Rect>;
  defaultMode?: LayoutMode;
  /** Called after any size change so canvases redraw at their new size. */
  onChange: () => void;
}

interface Panel {
  id: string;
  section: HTMLElement;
  head: HTMLElement;
  body: HTMLElement;
  collapse: HTMLButtonElement;
  rect: Rect;
  min: Size;
}

interface DragState {
  panel: Panel;
  kind: "move" | "resize";
  pointerId: number;
  startX: number;
  startY: number;
  base: Rect;
  preview: Rect | null;
}

const WIDE = "(min-width: 900px)";
const COLLAPSED_H = 2; // rows; enough for the header on one line

export class Workspace {
  readonly el: HTMLElement;
  private panels: Panel[] = [];
  private choice: LayoutMode;
  private mode: LayoutMode;
  private wide = window.matchMedia(WIDE);
  private ghost: HTMLDivElement;
  private drag: DragState | null = null;
  private lastWidth = -1;
  private pending = 0;

  constructor(private opts: WorkspaceOptions) {
    const mins: Record<string, Size> = {};
    for (const spec of opts.specs) mins[spec.id] = { w: spec.minW ?? 3, h: spec.minH ?? 3 };
    const saved = this.load(mins, opts.defaultMode ?? "free");
    this.choice = saved.mode;
    this.mode = this.wide.matches ? this.choice : "stack";

    this.el = el("main", { class: `workspace ${this.mode}` });
    for (const spec of opts.specs) this.panels.push(this.build(spec, saved.panels[spec.id]));
    this.ghost = el("div", { class: "panel-drop", hidden: "" }) as HTMLDivElement;
    this.el.append(this.ghost);

    this.wide.addEventListener("change", () => this.applyViewport());
    new ResizeObserver(() => this.scheduleLayout()).observe(this.el);
    this.layout();
  }

  get modeChoice(): LayoutMode {
    return this.choice;
  }

  get activeMode(): LayoutMode {
    return this.mode;
  }

  get isWide(): boolean {
    return this.wide.matches;
  }

  setMode(mode: LayoutMode) {
    if (mode === "free" && !this.isWide) return;
    this.choice = mode;
    this.applyViewport();
    this.persist();
  }

  reset() {
    for (const p of this.panels) {
      const base = this.opts.defaults[p.id];
      p.rect = clampRect({ ...base }, COLS, p.min.w, p.min.h);
      this.applyCollapsed(p);
    }
    this.choice = this.opts.defaultMode ?? "free";
    this.applyViewport();
    this.persist();
  }

  /** The page was hidden (display:none) and is visible again. */
  refresh() {
    if (this.el.clientWidth <= 0) return;
    this.lastWidth = -1;
    this.layout();
    this.opts.onChange();
  }

  /** Language changed: the runtime-written titles must follow. */
  relabel() {
    for (const p of this.panels) this.applyCollapsed(p);
  }

  private load(mins: Record<string, Size>, defaultMode: LayoutMode): LayoutState {
    let raw: unknown = null;
    try {
      raw = JSON.parse(localStorage.getItem(this.opts.storageKey) ?? "null");
    } catch {
      raw = null;
    }
    return readLayout(raw, this.opts.defaults, mins, defaultMode);
  }

  private build(spec: PanelSpec, rect: Rect): Panel {
    const section = spec.view;
    section.classList.add("panel");
    section.dataset.panel = spec.id;
    let head = section.querySelector<HTMLElement>(":scope > h2");
    if (!head) {
      head = el("h2");
      section.prepend(head);
    }
    head.classList.add("panel-head");

    const body = el("div", { class: "panel-body" });
    while (head.nextSibling) body.append(head.nextSibling);
    head.after(body);

    const collapse = el("button", {
      class: "panel-collapse icon",
      type: "button",
      title: "Collapse panel",
      "aria-label": "Collapse panel",
      "aria-expanded": "true",
    }, "⌄") as HTMLButtonElement;
    head.append(el("span", { class: "panel-actions" }, collapse));
    section.append(el("span", { class: "panel-resize", title: "Drag to resize", "aria-hidden": "true" }));

    const panel: Panel = {
      id: spec.id,
      section,
      head,
      body,
      collapse,
      rect: { ...rect },
      min: { w: spec.minW ?? 3, h: spec.minH ?? 3 },
    };
    collapse.onclick = (ev) => {
      ev.stopPropagation();
      this.toggle(panel);
    };
    head.addEventListener("pointerdown", (ev) => this.start(ev, panel, "move"));
    (section.querySelector(".panel-resize") as HTMLElement).addEventListener("pointerdown", (ev) => this.start(ev, panel, "resize"));
    this.el.append(section);
    this.applyCollapsed(panel);
    return panel;
  }

  private applyCollapsed(p: Panel) {
    const collapsed = !!p.rect.collapsed;
    p.section.classList.toggle("collapsed", collapsed);
    p.collapse.textContent = collapsed ? "⌃" : "⌄";
    p.collapse.title = t(collapsed ? "Expand panel" : "Collapse panel");
    p.collapse.setAttribute("aria-label", p.collapse.title);
    p.collapse.setAttribute("aria-expanded", String(!collapsed));
  }

  private toggle(p: Panel) {
    if (!p.rect.collapsed) {
      p.rect.expandedH = p.rect.h;
      p.rect.h = COLLAPSED_H;
      p.rect.collapsed = true;
      this.applyResolved(compact(this.panelRects()));
    } else {
      p.rect.collapsed = false;
      p.rect.h = Math.max(p.min.h, p.rect.expandedH ?? this.opts.defaults[p.id]?.h ?? 6);
      p.rect.expandedH = undefined;
      this.applyResolved(resolve(this.panelRects(), p.id).panels);
    }
    this.applyCollapsed(p);
    this.layout();
    this.persist();
    this.opts.onChange();
  }

  private start(ev: PointerEvent, panel: Panel, kind: "move" | "resize") {
    if (this.mode !== "free" || ev.button !== 0) return;
    if (kind === "resize" && panel.rect.collapsed) return;
    if (kind === "move" && (ev.target as Element).closest("button, select, input, a, label")) return;
    ev.preventDefault();
    ev.stopPropagation();
    this.drag = {
      panel,
      kind,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      base: { ...panel.rect },
      preview: null,
    };
    panel.section.classList.add("dragging");
    document.body.classList.add("panel-dragging");
    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
    window.addEventListener("pointercancel", this.onUp);
  }

  private onMove = (ev: PointerEvent) => {
    const d = this.drag;
    if (!d || ev.pointerId !== d.pointerId) return;
    const m = this.metrics();
    const pitch = (m.innerWidth + GAP) / COLS;
    const delta = { x: (ev.clientX - d.startX) / pitch, y: (ev.clientY - d.startY) / ROW_H };
    if (d.kind === "move") {
      const others = this.panels.filter((p) => p !== d.panel).map((p) => p.rect);
      const zone = dockZone(ev.clientX, ev.clientY, m);
      d.preview = moveRect(d.base, delta, zone, {
        bottom: bottomRow(others),
        minW: d.panel.min.w,
        minH: 1,
      });
      this.showGhost(d.preview, m);
    } else {
      const next = resizeRect(d.base, delta, { minW: d.panel.min.w, minH: d.panel.min.h });
      d.panel.rect = { ...d.panel.rect, w: next.w, h: next.h };
      this.layout();
    }
  };

  private onUp = (ev: PointerEvent) => {
    const d = this.drag;
    if (!d) return;
    if (ev.type === "pointerup" && ev.pointerId !== d.pointerId) return;
    this.drag = null;
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    window.removeEventListener("pointercancel", this.onUp);
    d.panel.section.classList.remove("dragging");
    document.body.classList.remove("panel-dragging");
    this.ghost.hidden = true;
    if (d.kind === "move" && d.preview) this.commit(d.panel, d.preview);
    else if (d.kind === "resize") this.commit(d.panel, d.panel.rect);
    this.persist();
    this.opts.onChange();
  };

  private commit(panel: Panel, rect: Rect) {
    panel.rect = { ...panel.rect, ...rect };
    this.applyResolved(resolve(this.panelRects(), panel.id).panels);
    this.layout();
  }

  private showGhost(rect: Rect, m: Metrics) {
    const px = rectToPixels(rect, m);
    const s = this.ghost.style;
    s.left = `${px.left}px`;
    s.top = `${px.top}px`;
    s.width = `${px.width}px`;
    s.height = `${px.height}px`;
    this.ghost.hidden = false;
  }

  private panelRects(): PanelRect[] {
    return this.panels.map((p) => ({ id: p.id, ...p.rect }));
  }

  private applyResolved(list: PanelRect[]) {
    for (const r of list) {
      const p = this.panels.find((q) => q.id === r.id);
      if (!p) continue;
      p.rect = { ...p.rect, x: r.x, y: r.y, w: r.w, h: r.h };
    }
  }

  private metrics(): Metrics {
    const cs = getComputedStyle(this.el);
    const left = parseFloat(cs.paddingLeft) || 0;
    const top = parseFloat(cs.paddingTop) || 0;
    const right = parseFloat(cs.paddingRight) || 0;
    const bottom = parseFloat(cs.paddingBottom) || 0;
    const innerWidth = Math.max(1, this.el.clientWidth - left - right);
    const box = this.el.getBoundingClientRect();
    const originX = box.left + this.el.clientLeft + left;
    const originY = box.top + this.el.clientTop + top;
    const content = Math.max(0, this.el.clientHeight - top - bottom);
    // A short workspace still accepts a bottom dock near the viewport's foot.
    const height = Math.max(content, window.innerHeight - originY);
    return { left, top, innerWidth, height, originX, originY };
  }

  private layout() {
    if (this.mode !== "free") {
      this.el.style.height = "";
      for (const p of this.panels) {
        const s = p.section.style;
        s.left = s.top = s.width = s.height = "";
        s.position = "";
      }
      return;
    }
    const m = this.metrics();
    for (const p of this.panels) {
      const px = rectToPixels(p.rect, m);
      const s = p.section.style;
      s.left = `${px.left}px`;
      s.top = `${px.top}px`;
      s.width = `${px.width}px`;
      s.height = p.rect.collapsed ? "auto" : `${px.height}px`;
    }
    const bottom = parseFloat(getComputedStyle(this.el).paddingBottom) || 0;
    this.el.style.height = `${m.top + bottomRow(this.panels.map((p) => p.rect)) * ROW_H + bottom}px`;
  }

  private applyViewport() {
    this.mode = this.wide.matches ? this.choice : "stack";
    this.el.classList.toggle("free", this.mode === "free");
    this.el.classList.toggle("stack", this.mode === "stack");
    this.layout();
    this.opts.onChange();
  }

  private scheduleLayout() {
    if (this.pending) return;
    this.pending = requestAnimationFrame(() => {
      this.pending = 0;
      if (this.el.clientWidth <= 0 || this.el.clientWidth === this.lastWidth) return;
      this.lastWidth = this.el.clientWidth;
      if (this.mode === "free") this.layout();
      this.opts.onChange();
    });
  }

  private persist() {
    const panels: Record<string, Rect> = {};
    for (const p of this.panels) {
      panels[p.id] = {
        x: p.rect.x,
        y: p.rect.y,
        w: p.rect.w,
        h: p.rect.h,
        collapsed: !!p.rect.collapsed,
        expandedH: p.rect.expandedH,
      };
    }
    try {
      localStorage.setItem(this.opts.storageKey, JSON.stringify({ v: LAYOUT_VERSION, mode: this.choice, panels }));
    } catch {
      /* storage unavailable */
    }
  }
}
