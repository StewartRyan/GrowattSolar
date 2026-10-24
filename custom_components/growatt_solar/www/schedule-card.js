const DAY = 1440;
const LANE = 46;
const MIN_DRAG = 5;
const MIN_CREATE = 15;

class GrowattScheduleCard extends HTMLElement {
  static getConfigElement() {
    return null;
  }

  setConfig(config) {
    if (!Array.isArray(config.periods) || config.periods.length === 0) {
      throw new Error("Add at least one schedule period");
    }
    for (const period of config.periods) {
      if (!period.enable || !period.start || !period.end) {
        throw new Error("Each period needs enable, start, and end");
      }
    }
    for (const period of config.avoid?.periods || []) {
      if (!period.enable || !period.start || !period.end) {
        throw new Error("Each avoided period needs enable, start, and end");
      }
    }
    this._config = {
      title: "Schedule",
      label: "Window",
      max: config.periods.length,
      color: "var(--primary-color)",
      suggestion: { start: "02:00:00", end: "06:00:00" },
      ...config,
    };
    this._draft = {};
  }

  set hass(hass) {
    this._hass = hass;
    if (this._drag) return;
    const signature = this._signature();
    if (signature === this._signatureSeen) return;
    this._signatureSeen = signature;
    this._paint();
  }

  getCardSize() {
    return 5;
  }

  connectedCallback() {
    if (this._root) return;
    this.attachShadow({ mode: "open" });
    this._root = this.shadowRoot;
    this._root.innerHTML = `
      <style>
        ha-card { padding: 16px 16px 12px; }
        .head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
        .title { font-size: 16px; font-weight: 600; line-height: 1.2; }
        .sub { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; }
        .add {
          margin-left: auto;
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: color-mix(in srgb, var(--bar) 16%, transparent);
          color: var(--primary-text-color);
          font: inherit;
          font-weight: 600;
          cursor: pointer;
        }
        .add:disabled { opacity: 0.4; cursor: default; }
        .pending { display: flex; align-items: center; gap: 8px; margin: 0 0 12px; }
        .pending[hidden] { display: none; }
        .pending-note { color: var(--secondary-text-color); font-size: 13px; margin-right: auto; }
        .discard, .apply {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          font: inherit;
          font-weight: 600;
          cursor: pointer;
        }
        .discard { background: var(--secondary-background-color, rgba(127,127,127,0.15)); color: var(--primary-text-color); }
        .apply { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .discard:disabled, .apply:disabled { opacity: 0.45; cursor: default; }
        .scale { position: relative; height: 18px; margin: 0 2px 4px; color: var(--secondary-text-color); font-size: 11px; }
        .scale span { position: absolute; transform: translateX(-50%); }
        .scale span:first-child { transform: none; }
        .scale span:last-child { transform: translateX(-100%); }
        .track {
          position: relative;
          border-radius: 12px;
          background:
            repeating-linear-gradient(90deg, transparent, transparent calc(100% / 24 - 1px), var(--divider-color) calc(100% / 24 - 1px), var(--divider-color) calc(100% / 24)),
            color-mix(in srgb, var(--primary-text-color) 6%, transparent);
          touch-action: none;
          cursor: crosshair;
          user-select: none;
        }
        .now {
          position: absolute;
          top: 0;
          bottom: 0;
          width: 2px;
          margin-left: -1px;
          background: var(--primary-text-color);
          opacity: 0.45;
          pointer-events: none;
          z-index: 3;
        }
        .bar {
          position: absolute;
          border-radius: 9px;
          background: var(--bar);
          color: var(--text-primary-color, #fff);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.01em;
          box-shadow: 0 1px 2px color-mix(in srgb, var(--bar) 40%, transparent);
          cursor: grab;
          overflow: hidden;
          min-width: 8px;
        }
        .bar.off {
          background: color-mix(in srgb, var(--bar) 28%, var(--card-background-color));
          color: var(--primary-text-color);
          box-shadow: inset 0 0 0 1.5px var(--bar);
        }
        .bar.ghost { opacity: 0.55; pointer-events: none; }
        .bar.busy { cursor: progress; }
        .handle {
          position: absolute;
          top: 0;
          bottom: 0;
          width: 14px;
          cursor: ew-resize;
        }
        .handle::after {
          content: "";
          position: absolute;
          top: 8px;
          bottom: 8px;
          width: 2px;
          border-radius: 2px;
          background: currentColor;
          opacity: 0.75;
        }
        .handle.start { left: 0; }
        .handle.start::after { left: 5px; }
        .handle.end { right: 0; }
        .handle.end::after { right: 5px; }
        .rows { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
        .row {
          display: flex;
          align-items: center;
          gap: 10px;
          min-height: 36px;
        }
        .swatch { width: 10px; height: 10px; border-radius: 99px; background: var(--bar); flex: none; }
        .swatch.off { background: transparent; box-shadow: inset 0 0 0 1.5px var(--bar); }
        .when { font-variant-numeric: tabular-nums; font-weight: 600; }
        .name { color: var(--secondary-text-color); font-size: 13px; }
        .spacer { flex: 1; }
        button.quiet, button.warn {
          border: none;
          background: var(--secondary-background-color, rgba(127,127,127,0.15));
          color: var(--primary-text-color);
          border-radius: 999px;
          padding: 6px 12px;
          font: inherit;
          cursor: pointer;
        }
        button.warn { color: var(--error-color); }
        button:disabled { opacity: 0.45; cursor: default; }
        .hint, .error { margin: 10px 2px 0; font-size: 12px; line-height: 1.4; }
        .hint { color: var(--secondary-text-color); }
        .error { color: var(--error-color); }
        .saving {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 12px;
          background: color-mix(in srgb, var(--card-background-color) 55%, transparent);
          font-weight: 600;
          z-index: 4;
        }
      </style>
      <ha-card>
        <div class="head">
          <div>
            <div class="title"></div>
            <div class="sub"></div>
          </div>
          <button class="add" type="button">Add window</button>
        </div>
        <div class="pending" hidden>
          <span class="pending-note"></span>
          <button class="discard" type="button">Discard</button>
          <button class="apply" type="button">Apply</button>
        </div>
        <div class="scale"></div>
        <div class="track"></div>
        <div class="rows"></div>
        <div class="hint">Drag across the day to add a window. Drag a window to move it, or either end to change its length. Nothing is sent to the inverter until you apply.</div>
        <div class="error"></div>
      </ha-card>
    `;
    this._title = this._root.querySelector(".title");
    this._sub = this._root.querySelector(".sub");
    this._add = this._root.querySelector(".add");
    this._scale = this._root.querySelector(".scale");
    this._track = this._root.querySelector(".track");
    this._rows = this._root.querySelector(".rows");
    this._error = this._root.querySelector(".error");
    this._scale.innerHTML = ["00", "06", "12", "18", "24"]
      .map((label, index) => `<span style="left:${index * 25}%">${label}</span>`)
      .join("");
    this._pending = this._root.querySelector(".pending");
    this._pendingNote = this._root.querySelector(".pending-note");
    this._apply = this._root.querySelector(".apply");
    this._discard = this._root.querySelector(".discard");
    this._add.addEventListener("click", () => this._addWindow());
    this._apply.addEventListener("click", () => this._applyChanges());
    this._discard.addEventListener("click", () => this._discardChanges());
    this._track.addEventListener("pointerdown", (event) => this._pointerDown(event));
    this._track.addEventListener("pointermove", (event) => this._pointerMove(event));
    this._track.addEventListener("pointerup", (event) => this._pointerUp(event));
    this._track.addEventListener("pointercancel", () => this._cancelDrag());
    this.style.setProperty("--bar", this._config.color);
    this._paint();
  }

  _signature() {
    return [...(this._config?.periods || []), ...(this._config?.avoid?.periods || [])]
      .map((period) => [period.enable, period.start, period.end].map((id) => this._state(id)).join(","))
      .join("|");
  }

  _state(entityId) {
    return this._hass?.states[entityId]?.state ?? "unavailable";
  }

  _live(index) {
    const period = this._config.periods[index];
    return {
      start: minutes(this._state(period.start)),
      end: minutes(this._state(period.end)),
      enabled: this._state(period.enable) === "on",
    };
  }

  _periods() {
    return this._config.periods.map((period, index) => {
      const preview = this._preview?.index === index ? this._preview : null;
      const pending = this._draft[index];
      const live = this._live(index);
      const start = preview ? preview.start : pending ? pending.start : live.start;
      const end = preview ? preview.end : pending ? pending.end : live.end;
      const enabled = preview ? live.enabled : pending ? pending.enabled : live.enabled;
      const known = [period.enable, period.start, period.end].every((id) => {
        const value = this._state(id);
        return value !== "unavailable" && value !== "unknown";
      });
      const empty = known && !enabled && start === 0 && end === 0;
      return { ...period, index, start, end, enabled, known, empty, shown: known && !empty };
    });
  }

  _paint() {
    if (!this._track || !this._config) return;
    const periods = this._periods();
    const shown = periods.filter((period) => period.shown);
    const lanes = layout(shown);
    const used = shown.length;
    const pending = Object.keys(this._draft).length;
    this._title.textContent = this._config.title;
    this._sub.textContent = this._busy ? "Saving to the inverter…" : `${used} of ${this._config.max} windows`;
    this._pending.hidden = pending === 0;
    this._pendingNote.textContent = pending === 1 ? "1 unsaved change" : `${pending} unsaved changes`;
    this._apply.disabled = this._busy;
    this._discard.disabled = this._busy;
    this._add.disabled = this._busy || used >= this._config.max || !periods.some((period) => period.empty);
    this._track.style.height = `${Math.max(lanes, 1) * LANE}px`;
    const bars = [];
    for (const period of shown) {
      for (const segment of segments(period)) {
        bars.push(bar(period, segment));
      }
    }
    if (this._preview?.create) {
      const ghost = normalize(this._preview.anchor, this._preview.current);
      for (const segment of segments({ start: ghost.start, end: ghost.end, lane: 0 })) {
        bars.push(
          `<div class="bar ghost" style="left:${pct(segment.from)};width:${pct(segment.to - segment.from)};top:6px;height:${LANE - 12}px"></div>`,
        );
      }
    }
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const saving = this._busy ? `<div class="saving">Saving…</div>` : "";
    this._track.innerHTML =
      bars.join("") + `<div class="now" style="left:${pct(nowMin)}"></div>` + saving;
    this._rows.innerHTML = shown
      .map(
        (period) => `
        <div class="row">
          <span class="swatch ${period.enabled ? "" : "off"}"></span>
          <span class="when">${label(period.start)}–${label(period.end)}</span>
          <span class="name">${escapeHtml(period.name || `Window ${period.index + 1}`)}${period.enabled ? "" : " · off"}</span>
          <span class="spacer"></span>
          <button class="quiet" type="button" data-act="toggle" data-index="${period.index}" ${this._busy ? "disabled" : ""}>${period.enabled ? "Turn off" : "Turn on"}</button>
          <button class="warn" type="button" data-act="remove" data-index="${period.index}" ${this._busy ? "disabled" : ""}>Remove</button>
        </div>`,
      )
      .join("");
    this._rows.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => this._rowAction(button.dataset.act, Number(button.dataset.index)));
    });
    this._error.textContent = this._message || "";
  }

  _pointerDown(event) {
    if (this._busy || event.button > 0) return;
    const minute = this._minute(event);
    const hit = this._hit(event, minute);
    if (hit) {
      const period = this._periods()[hit.index];
      this._drag = {
        type: hit.edge ? "resize" : "move",
        edge: hit.edge,
        index: hit.index,
        origin: minute,
        start: period.start,
        end: period.end,
      };
    } else if (this._periods().filter((period) => period.shown).length < this._config.max) {
      this._drag = { type: "create", anchor: minute, current: minute };
      this._preview = { create: true, anchor: minute, current: minute };
    } else {
      this._message = "All 3 windows are already in use. Remove one to add another.";
    }
    this._track.setPointerCapture(event.pointerId);
    this._paint();
  }

  _pointerMove(event) {
    if (!this._drag) return;
    const minute = this._minute(event);
    if (this._drag.type === "create") {
      this._drag.current = minute;
      this._preview = { create: true, anchor: this._drag.anchor, current: minute };
    } else {
      const next =
        this._drag.type === "move"
          ? shift(this._drag.start, this._drag.end, minute - this._drag.origin)
          : resize(this._drag.start, this._drag.end, this._drag.edge, minute);
      const neighbours = [
        ...this._periods().filter((period) => period.index !== this._drag.index),
        ...this._others(),
      ];
      const pulled = magnet(next, neighbours, this._drag.type === "move" ? null : this._drag.edge);
      this._preview = { index: this._drag.index, ...pulled };
    }
    this._paint();
  }

  _pointerUp(event) {
    if (!this._drag) return;
    const drag = this._drag;
    this._drag = null;
    this._track.releasePointerCapture?.(event.pointerId);
    if (drag.type === "create") {
      const created = normalize(drag.anchor, drag.current);
      const length = span(created.start, created.end);
      this._preview = null;
      if (length < MIN_CREATE) {
        this._paint();
        return;
      }
      const slot = this._periods().find((period) => period.empty);
      if (!slot) return;
      this._stage(slot, created.start, created.end, true);
      return;
    }
    const next = this._preview;
    this._preview = null;
    const period = this._periods()[drag.index];
    if (!next || (next.start === period.start && next.end === period.end)) {
      this._paint();
      return;
    }
    this._stage(period, next.start, next.end, period.enabled);
  }

  _cancelDrag() {
    this._drag = null;
    this._preview = null;
    this._paint();
  }

  _minute(event) {
    const rect = this._track.getBoundingClientRect();
    const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
    return Math.max(0, Math.min(DAY - 1, Math.round(ratio * DAY)));
  }

  _hit(event, minute) {
    const rect = this._track.getBoundingClientRect();
    const y = event.clientY - rect.top;
    const target = event.target.closest?.(".bar");
    if (!target) return null;
    const index = Number(target.dataset.index);
    const edge = event.target.dataset?.edge || null;
    if (Number.isNaN(index)) return null;
    const lane = Math.floor(y / LANE);
    if (lane !== Number(target.dataset.lane)) return { index, edge };
    return { index, edge };
  }

  _others() {
    const avoid = this._config.avoid;
    if (!avoid) return [];
    return avoid.periods.map((period, index) => ({
      ...windowState(this._hass, period, index),
      name: `${avoid.label || "Other"} ${index + 1}`,
    }));
  }

  _named(period) {
    return { ...period, name: `${this._config.label} ${period.index + 1}` };
  }

  async _addWindow() {
    const periods = this._periods();
    const slot = periods.find((period) => period.empty);
    if (!slot || this._busy) return;
    const taken = [...periods, ...this._others()].filter((period) => period.shown);
    const placed = place(taken, this._config.suggestion);
    if (!placed) {
      this._message = "There is no free time left for another window.";
      this._paint();
      return;
    }
    this._stage(slot, placed.start, placed.end, true);
  }

  _rowAction(action, index) {
    const period = this._periods()[index];
    if (!period || this._busy) return;
    if (action === "toggle") {
      this._stage(period, period.start, period.end, !period.enabled);
      return;
    }
    this._stage(period, 0, 0, false);
  }

  _discardChanges() {
    if (this._busy) return;
    this._draft = {};
    this._message = "";
    this._paint();
  }

  async _applyChanges() {
    const changes = Object.entries(this._draft).map(([index, draft]) => ({
      key: Number(index),
      ...draft,
      config: this._config.periods[index],
      live: this._live(Number(index)),
    }));
    if (!changes.length || this._busy) return;
    this._busy = true;
    this._message = "";
    this._drag = { type: "lock" };
    this._paint();
    try {
      await applyChanges(this._hass, changes, (change) => {
        delete this._draft[change.key];
      });
    } catch (err) {
      this._message = message(err);
    } finally {
      this._busy = false;
      this._drag = null;
      this._signatureSeen = "";
      this._paint();
    }
  }

  _stage(period, start, end, enabled) {
    const others = [
      ...this._periods()
        .filter((other) => other.index !== period.index)
        .map((other) => this._named(other)),
      ...this._others(),
    ];
    const conflict = clash({ start, end, enabled }, others);
    if (conflict) {
      this._message = clashMessage(conflict);
      this._preview = null;
      this._paint();
      return;
    }
    const live = this._live(period.index);
    if (start === live.start && end === live.end && enabled === live.enabled) delete this._draft[period.index];
    else this._draft[period.index] = { start, end, enabled };
    this._message = "";
    this._preview = null;
    this._paint();
  }
}

function minutes(state) {
  const match = /^(\d{1,2}):(\d{2})/.exec(state || "");
  if (!match) return 0;
  return Number(match[1]) * 60 + Number(match[2]);
}

function clock(total) {
  const value = ((total % DAY) + DAY) % DAY;
  const hour = Math.floor(value / 60);
  const minute = value % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`;
}

function label(total) {
  return clock(total).slice(0, 5);
}

function pct(minute) {
  return `${(minute / DAY) * 100}%`;
}

function span(start, end) {
  return (end - start + DAY) % DAY;
}

function shift(start, end, delta) {
  const length = span(start, end);
  const next = (start + delta + DAY * 8) % DAY;
  return { start: next, end: (next + length) % DAY };
}

function resize(start, end, edge, minute) {
  const wrapped = end < start || (end === 0 && start > 0);
  if (!wrapped) {
    if (edge === "start") return { start: Math.max(0, Math.min(minute, end - MIN_DRAG)), end };
    return { start, end: Math.min(DAY - 1, Math.max(minute, start + MIN_DRAG)) };
  }
  if (edge === "start") {
    const latest = Math.min(DAY - 1, end + DAY - MIN_DRAG);
    return { start: Math.max(end + 1, Math.min(minute, latest)), end };
  }
  const earliest = Math.max(0, start + MIN_DRAG - DAY);
  return { start, end: Math.min(start - 1, Math.max(minute, earliest)) };
}

function normalize(anchor, current) {
  const start = Math.min(anchor, current);
  const end = Math.max(anchor, current);
  return { start, end };
}

function segments(period) {
  const top = period.lane * LANE + 6;
  const height = LANE - 12;
  if (period.end > period.start) {
    return [{ from: period.start, to: period.end, edgeStart: true, edgeEnd: true, top, height }];
  }
  if (period.start === period.end) return [];
  return [
    { from: period.start, to: DAY, edgeStart: true, edgeEnd: false, top, height },
    { from: 0, to: period.end, edgeStart: false, edgeEnd: true, top, height },
  ];
}

function bar(period, segment) {
  const handles = [
    segment.edgeStart ? `<div class="handle start" data-edge="start"></div>` : "",
    segment.edgeEnd ? `<div class="handle end" data-edge="end"></div>` : "",
  ].join("");
  const text = segment.to - segment.from > 90 ? `${label(period.start)}–${label(period.end)}` : "";
  return `<div class="bar ${period.enabled ? "" : "off"}" data-index="${period.index}" data-lane="${period.lane}" style="left:${pct(segment.from)};width:${pct(segment.to - segment.from)};top:${segment.top}px;height:${segment.height}px">${handles}${text}</div>`;
}

function layout(periods) {
  const lanes = [];
  for (const period of periods) {
    let lane = lanes.findIndex((members) => members.every((other) => !overlaps(period, other)));
    if (lane < 0) {
      lane = lanes.length;
      lanes.push([]);
    }
    lanes[lane].push(period);
    period.lane = lane;
  }
  return lanes.length;
}

function overlaps(left, right) {
  return blocks(left).some((a) => blocks(right).some((b) => a[0] < b[1] && b[0] < a[1]));
}

function blocks(period) {
  if (period.end > period.start) return [[period.start, period.end]];
  if (period.start === period.end) return [];
  return [
    [period.start, DAY],
    [0, period.end],
  ];
}

// The inverter treats both the start and end minute as part of a window.
function covered(start, end) {
  const used = new Set();
  if (start === end) return used;
  const length = span(start, end);
  for (let step = 0; step <= length; step += 1) used.add((start + step) % DAY);
  return used;
}

function clash(candidate, others) {
  if (!candidate.enabled) return null;
  const mine = covered(candidate.start, candidate.end);
  for (const other of others) {
    if (!other.enabled || !other.shown) continue;
    for (const minute of covered(other.start, other.end)) {
      if (mine.has(minute)) return { other, minute };
    }
  }
  return null;
}

const MAGNET = 10;

function distance(left, right) {
  const gap = Math.abs(left - right) % DAY;
  return Math.min(gap, DAY - gap);
}

// Pulls a dragged edge to the minute just outside a neighbouring window.
function magnet(next, neighbours, edge) {
  const length = span(next.start, next.end);
  let best = null;
  for (const other of neighbours) {
    if (!other.shown) continue;
    const after = (other.end + 1) % DAY;
    const before = (other.start - 1 + DAY) % DAY;
    const options = [];
    if (edge !== "end") {
      options.push({
        gap: distance(next.start, after),
        start: after,
        end: edge === "start" ? next.end : (after + length) % DAY,
      });
    }
    if (edge !== "start") {
      options.push({
        gap: distance(next.end, before),
        start: edge === "end" ? next.start : (before - length + DAY) % DAY,
        end: before,
      });
    }
    for (const option of options) {
      if (option.gap > MAGNET || (best && option.gap >= best.gap)) continue;
      if (edge && span(option.start, option.end) < MIN_DRAG) continue;
      if (edge && option.end < option.start !== next.end < next.start) continue;
      best = option;
    }
  }
  return best ? { start: best.start, end: best.end } : next;
}

// Moves a dragged window to the closest spot that touches no other enabled window.
function settle(next, others, edge) {
  const fits = (start, end) => start !== end && !clash({ start, end, enabled: true }, others);
  if (fits(next.start, next.end)) return next;
  const blockers = others.filter((other) => other.enabled && other.shown);
  const options = [];
  if (!edge) {
    const length = span(next.start, next.end);
    for (const other of blockers) {
      const after = (other.end + 1) % DAY;
      const before = (other.start - 1 - length + DAY * 2) % DAY;
      options.push({ start: after, end: (after + length) % DAY });
      options.push({ start: before, end: (before + length) % DAY });
    }
  } else if (edge === "end") {
    const reach = span(next.start, next.end);
    for (const other of blockers) {
      const end = (other.start - 1 + DAY) % DAY;
      const length = span(next.start, end);
      if (length > 0 && length <= reach) options.push({ start: next.start, end });
    }
  } else {
    const reach = span(next.start, next.end);
    for (const other of blockers) {
      const start = (other.end + 1) % DAY;
      const length = span(start, next.end);
      if (length > 0 && length <= reach) options.push({ start, end: next.end });
    }
  }
  let best = null;
  for (const option of options) {
    if (!fits(option.start, option.end)) continue;
    const gap =
      edge === "end" ? distance(option.end, next.end) : distance(option.start, next.start);
    if (!best || gap < best.gap) best = { ...option, gap };
  }
  return best && { start: best.start, end: best.end };
}

function clashMessage({ other, minute }) {
  return `${label(minute)} is already used by ${other.name} (${label(other.start)}–${label(other.end)}). Windows cannot share a minute.`;
}

function fitsChange(change, pending, committed) {
  if (!change.enabled) return true;
  const blockers = [
    ...pending
      .filter((other) => other !== change && other.live.enabled)
      .map((other) => ({ start: other.live.start, end: other.live.end, enabled: true, shown: true })),
    ...committed.filter((other) => other.enabled),
  ];
  return !clash(change, blockers);
}

async function applyChanges(hass, changes, onApplied) {
  const pending = changes.slice();
  const committed = [];
  while (pending.length) {
    let index = pending.findIndex((change) => fitsChange(change, pending, committed));
    if (index < 0) index = 0;
    const [change] = pending.splice(index, 1);
    await hass.callService("growatt_solar", "set_window", {
      start_entity: change.config.start,
      end_entity: change.config.end,
      enable_entity: change.config.enable,
      start: clock(change.start),
      end: clock(change.end),
      enabled: change.enabled,
    });
    committed.push({ start: change.start, end: change.end, enabled: change.enabled, shown: true });
    onApplied(change);
  }
}

function place(taken, suggestion) {
  const busy = new Set();
  for (const period of taken) {
    for (const minute of covered(period.start, period.end)) busy.add(minute);
  }
  const wanted = { start: minutes(suggestion.start), end: minutes(suggestion.end) };
  if (![...covered(wanted.start, wanted.end)].some((minute) => busy.has(minute))) return wanted;
  if (busy.size >= DAY) return null;
  let first = 0;
  while (!busy.has(first)) first += 1;
  let best = null;
  let runStart = null;
  let runLength = 0;
  for (let step = 1; step <= DAY; step += 1) {
    const minute = (first + step) % DAY;
    if (busy.has(minute)) {
      if (runLength && (!best || runLength > best.length)) best = { start: runStart, length: runLength };
      runStart = null;
      runLength = 0;
    } else {
      if (runStart === null) runStart = minute;
      runLength += 1;
    }
  }
  if (!best || best.length <= MIN_CREATE) return null;
  const offset = (SNAP - (best.start % SNAP)) % SNAP;
  const room = best.length - offset > MIN_CREATE ? offset : 0;
  const start = (best.start + room) % DAY;
  const length = Math.min(120, best.length - room - 1);
  return { start, end: (start + length) % DAY };
}

function windowState(hass, period, index) {
  const read = (entityId) => hass?.states[entityId]?.state ?? "unavailable";
  const enable = read(period.enable);
  const startState = read(period.start);
  const endState = read(period.end);
  const known = [enable, startState, endState].every(
    (value) => value !== "unavailable" && value !== "unknown",
  );
  const start = minutes(startState);
  const end = minutes(endState);
  const enabled = enable === "on";
  const empty = known && !enabled && start === 0 && end === 0;
  return { config: period, index, start, end, enabled, known, empty, shown: known && !empty };
}

function message(err) {
  if (!err) return "The inverter did not keep that window.";
  if (typeof err === "string") return err;
  return err.message || err.body?.message || "The inverter did not keep that window.";
}

const SNAP = 5;

function snap(minute) {
  return Math.max(0, Math.min(DAY - 1, Math.round(minute / SNAP) * SNAP));
}

const POWER_ON_ICON = `<svg viewBox="0 0 24 24"><path fill="currentColor" d="M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 1 1-12.41-4.41L5.17 6.17A8.96 8.96 0 0 0 3 12a9 9 0 1 0 18 0 8.96 8.96 0 0 0-3.17-6.83z"/></svg>`;
const POWER_OFF_ICON = `<svg viewBox="0 0 24 24"><path fill="currentColor" d="M11 3h2v8h-2V3zm1 18a9 9 0 0 0 6.36-15.36l-1.41 1.41A7 7 0 1 1 6.05 7.05L4.64 5.64A9 9 0 0 0 12 21z"/></svg>`;
const DELETE_ICON = `<svg viewBox="0 0 24 24"><path fill="currentColor" d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

const SUN_STEP = 10;
const SUN_USEFUL = 0.08;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function solarStrength(minute, day, lat, lon, offsetHours) {
  const latRad = (lat * Math.PI) / 180;
  const decl = 0.4093 * Math.sin(((2 * Math.PI) / 365) * (day - 81));
  const b = ((2 * Math.PI) / 365) * (day - 81);
  const equation = 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b);
  const solarMinutes = minute + equation + 4 * lon - 60 * offsetHours;
  const hourAngle = ((solarMinutes - 720) * Math.PI) / 720;
  const sine =
    Math.sin(latRad) * Math.sin(decl) + Math.cos(latRad) * Math.cos(decl) * Math.cos(hourAngle);
  return Math.max(0, sine);
}

function dayOfYear(date) {
  return Math.floor((date - new Date(date.getFullYear(), 0, 0)) / 86400000);
}

function sampledCurve(at) {
  const samples = [];
  for (let minute = 0; minute <= DAY; minute += SUN_STEP) samples.push(at(Math.min(minute, DAY - 1)));
  return {
    at(minute) {
      const index = Math.max(0, Math.min(samples.length - 2, Math.floor(minute / SUN_STEP)));
      const mix = (minute - index * SUN_STEP) / SUN_STEP;
      return samples[index] * (1 - mix) + samples[index + 1] * mix;
    },
  };
}

function coordinateCurve(lat, lon, now) {
  const day = dayOfYear(now);
  const offset = -now.getTimezoneOffset() / 60;
  return sampledCurve((minute) => solarStrength(minute, day, lat, lon, offset));
}

function humpCurve(rise, set) {
  return sampledCurve((minute) => {
    if (minute <= rise || minute >= set) return 0;
    return Math.sin((Math.PI * (minute - rise)) / (set - rise));
  });
}

function clockMinute(iso, now) {
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return null;
  let adjusted = time;
  while (adjusted - now.getTime() > 18 * 3600000) adjusted -= 86400000;
  while (now.getTime() - adjusted > 18 * 3600000) adjusted += 86400000;
  const local = new Date(adjusted);
  return local.getHours() * 60 + local.getMinutes();
}

function resolveDaylight(config, hass, now) {
  const loc = config.location || {};
  const explicitLat = finiteNumber(config.latitude ?? loc.latitude);
  const explicitLon = finiteNumber(config.longitude ?? loc.longitude);
  if (explicitLat !== null && explicitLon !== null) {
    return { source: "card", curve: coordinateCurve(explicitLat, explicitLon, now) };
  }
  const lat = finiteNumber(hass?.config?.latitude);
  const lon = finiteNumber(hass?.config?.longitude);
  if (lat !== null && lon !== null && !(lat === 0 && lon === 0)) {
    return { source: "home", curve: coordinateCurve(lat, lon, now) };
  }
  const sun = hass?.states?.["sun.sun"]?.attributes || {};
  const rise = clockMinute(sun.next_rising, now);
  const set = clockMinute(sun.next_setting, now);
  if (rise !== null && set !== null && set > rise + 30) return { source: "sun", curve: humpCurve(rise, set) };
  return { source: "typical", curve: humpCurve(7 * 60, 19 * 60) };
}

function daylightSpan(start, end, curve) {
  const points = [];
  const collect = (from, to) => {
    const step = Math.max(5, Math.round(Math.max(to - from, 1) / 12));
    for (let minute = from; minute < to; minute += step) points.push(minute);
    points.push(Math.max(from, to - 1));
  };
  if (end > start) collect(start, end);
  else if (end < start) {
    collect(start, DAY);
    collect(0, end);
  } else return "night";
  let day = false;
  let night = false;
  for (const minute of points) {
    if (curve.at(minute) > SUN_USEFUL) day = true;
    else night = true;
  }
  if (day && night) return "both";
  return day ? "day" : "night";
}

const SCHEDULE_PHRASES = {
  charge: {
    day: {
      true: "Solar fills the battery, with the grid.",
      false: "Spare solar fills the battery.",
      unknown: "Solar fills the battery.",
    },
    night: {
      true: "The grid fills the battery.",
      false: "No solar, so it won't charge.",
      unknown: "No solar in this window.",
    },
    both: {
      true: "Solar by day, the grid after dark.",
      false: "Spare solar fills it while the sun is up.",
      unknown: "Fills from solar while the sun is up.",
    },
  },
  discharge: {
    day: "Solar runs the house. Spare goes out.",
    night: "The battery runs the house. Spare goes out.",
    both: "Solar by day, the battery after dark.",
    blocked: "Won't discharge. Stop is full.",
    idle: "Won't discharge. Power is off.",
  },
};

function daylightGradient(curve, scale) {
  const stops = [];
  for (let minute = 0; minute <= DAY; minute += 20) {
    const mix = Math.round(curve.at(Math.min(minute, DAY - 1)) * scale);
    const color =
      mix < 2
        ? "transparent"
        : `color-mix(in srgb, var(--amber-color, var(--warning-color)) ${mix}%, transparent)`;
    stops.push(`${color} ${((minute / DAY) * 100).toFixed(1)}%`);
  }
  return `linear-gradient(180deg, ${stops.join(",")})`;
}

class GrowattDayCard extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.kinds) || config.kinds.length === 0) {
      throw new Error("Add at least one kind of window");
    }
    for (const kind of config.kinds) {
      if (!kind.key || !kind.label || !Array.isArray(kind.periods) || kind.periods.length === 0) {
        throw new Error("Each kind needs a key, a label, and periods");
      }
      for (const period of kind.periods) {
        if (!period.enable || !period.start || !period.end) {
          throw new Error("Each period needs enable, start, and end");
        }
      }
    }
    this._config = { title: "Battery schedule", height: 720, ...config };
    this._draft = {};
    this._acDraft = null;
    this._limits = {};
  }

  set hass(hass) {
    this._hass = hass;
    if (this._drag) return;
    const signature = this._signature();
    if (signature === this._signatureSeen) return;
    this._signatureSeen = signature;
    this._paint();
  }

  getCardSize() {
    return 14;
  }

  getGridOptions() {
    return { columns: 12, rows: "auto", min_columns: 6 };
  }

  connectedCallback() {
    if (this._root) return;
    this.attachShadow({ mode: "open" });
    this._root = this.shadowRoot;
    this._root.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .head { display: flex; align-items: center; gap: 12px; }
        .title { font-size: 16px; font-weight: 600; line-height: 1.2; }
        .sub { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; }
        .actions { margin-left: auto; display: flex; gap: 8px; }
        .actions[hidden] { display: none; }
        .icon {
          width: 36px;
          height: 36px;
          border: none;
          border-radius: 999px;
          padding: 0;
          display: grid;
          place-items: center;
          cursor: pointer;
        }
        .icon svg { width: 20px; height: 20px; display: block; }
        .icon.discard { background: var(--secondary-background-color, rgba(127,127,127,0.18)); color: var(--primary-text-color); }
        .icon.apply { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .icon:disabled { opacity: 0.45; cursor: default; }
        .modes { display: grid; gap: 8px; margin-top: 14px; }
        .mode {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 12px;
          border-radius: 12px;
          background: color-mix(in srgb, var(--c) 10%, transparent);
        }
        .mode .dot { width: 10px; height: 10px; border-radius: 99px; background: var(--c); flex: none; }
        .mode-copy { flex: 1; min-width: 180px; }
        .mode-title { font-size: 13px; font-weight: 650; }
        .mode-detail {
          color: var(--secondary-text-color);
          font-size: 12px;
          line-height: 1.4;
          margin-top: 2px;
        }
        .behavior { color: var(--secondary-text-color); font-size: 13px; }
        .source-options { display: flex; gap: 4px; flex-wrap: wrap; }
        .source-option {
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          padding: 7px 11px;
          background: transparent;
          color: var(--primary-text-color);
          font: inherit;
          font-size: 12px;
          cursor: pointer;
        }
        .source-option.selected {
          border-color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 16%, transparent);
          color: var(--primary-color);
          font-weight: 650;
        }
        .source-option:disabled { opacity: 0.45; cursor: default; }
        @media (max-width: 600px) {
          .mode { align-items: flex-start; flex-wrap: wrap; }
          .mode .dot { margin-top: 4px; }
        }
        .kinds { display: flex; gap: 8px; margin: 14px 0 12px; flex-wrap: wrap; }
        .kind {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: color-mix(in srgb, var(--c) 16%, transparent);
          color: var(--primary-text-color);
          font: inherit;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
        }
        .kind .dot { width: 10px; height: 10px; border-radius: 99px; background: var(--c); }
        .kind .count { color: var(--secondary-text-color); font-weight: 400; }
        .kind:disabled { opacity: 0.4; cursor: default; }
        .day { display: grid; grid-template-columns: 40px 1fr; gap: 8px; }
        .hours { position: relative; color: var(--secondary-text-color); font-size: 11px; font-variant-numeric: tabular-nums; }
        .hours span { position: absolute; right: 0; transform: translateY(-50%); }
        .hours span:first-child { transform: none; }
        .hours span:last-child { transform: translateY(-100%); }
        .track {
          position: relative;
          border-radius: 12px;
          background:
            repeating-linear-gradient(180deg, transparent, transparent calc(100% / 24 - 1px), var(--divider-color) calc(100% / 24 - 1px), var(--divider-color) calc(100% / 24)),
            color-mix(in srgb, var(--primary-text-color) 5%, transparent);
          touch-action: pan-y;
          cursor: crosshair;
          user-select: none;
        }
        .daylight, .sunline { position: absolute; pointer-events: none; }
        .daylight { inset: 0; border-radius: 12px; }
        .sunline { top: 0; bottom: 0; left: 0; width: 7px; border-radius: 12px 0 0 12px; }
        .bar {
          position: absolute;
          z-index: 1;
          box-sizing: border-box;
          border-radius: 10px;
          background: var(--c);
          color: var(--text-primary-color, #fff);
          display: flex;
          flex-direction: column;
          justify-content: center;
          padding: 0 12px;
          overflow: hidden;
          font-size: 12px;
          line-height: 1.3;
          cursor: grab;
          touch-action: none;
          box-shadow: 0 1px 3px color-mix(in srgb, var(--c) 40%, transparent);
          min-height: 6px;
        }
        .bar { flex-direction: row; align-items: center; gap: 8px; }
        .bar .copy { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
        .bar .phrase { font-size: 12px; line-height: 1.25; opacity: 0.92; }
        .bar-actions {
          margin-left: auto;
          display: flex;
          align-items: center;
          gap: 2px;
          flex: none;
          position: relative;
          z-index: 2;
        }
        .bar-button {
          width: 28px;
          height: 28px;
          padding: 0;
          border: none;
          border-radius: 8px;
          display: grid;
          place-items: center;
          background: transparent;
          color: inherit;
          opacity: 0.8;
          cursor: pointer;
        }
        .bar-button svg { width: 16px; height: 16px; display: block; }
        .bar-button:hover, .bar-button:focus-visible {
          opacity: 1;
          background: color-mix(in srgb, currentColor 18%, transparent);
          outline: none;
        }
        .bar-button.warn:hover, .bar-button.warn:focus-visible {
          background: color-mix(in srgb, var(--error-color) 70%, transparent);
          color: var(--text-primary-color, #fff);
        }
        .bar-button:disabled { opacity: 0.35; cursor: default; background: transparent; }
        .controls {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px 16px;
          flex-wrap: wrap;
          margin: 14px 0 12px;
        }
        .controls .kinds { margin: 0; }
        .charge-source { display: flex; align-items: center; gap: 8px; }
        .charge-source[hidden] { display: none; }
        .charge-source .label, .policy .label { color: var(--secondary-text-color); font-size: 12px; }
        .policy {
          display: flex;
          flex-direction: column;
          gap: 4px;
          margin: 0 0 12px;
          padding: 10px 12px;
          border-radius: 12px;
          background: color-mix(in srgb, var(--c, var(--primary-color)) 10%, transparent);
        }
        .policy[hidden] { display: none; }
        .policy-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .policy .dot { width: 10px; height: 10px; border-radius: 99px; background: var(--c); flex: none; }
        .policy-title { font-size: 13px; font-weight: 650; }
        .policy-note { color: var(--secondary-text-color); font-size: 12px; line-height: 1.35; padding-left: 20px; }
        .stepper { display: inline-flex; align-items: center; gap: 2px; }
        .policy-row .stepper:first-of-type { margin-left: auto; }
        .stepper button {
          width: 28px;
          height: 28px;
          border: none;
          border-radius: 8px;
          background: transparent;
          color: var(--primary-text-color);
          font: inherit;
          font-size: 16px;
          line-height: 1;
          cursor: pointer;
        }
        .stepper button:hover, .stepper button:focus-visible {
          background: color-mix(in srgb, var(--primary-text-color) 10%, transparent);
          outline: none;
        }
        .stepper button:disabled { opacity: 0.35; cursor: default; background: transparent; }
        .stepper .value {
          min-width: 42px;
          text-align: center;
          font-variant-numeric: tabular-nums;
          font-size: 13px;
          font-weight: 650;
        }
        .segmented {
          display: inline-flex;
          padding: 2px;
          border-radius: 999px;
          background: color-mix(in srgb, var(--primary-text-color) 7%, transparent);
        }
        .segmented .source-option { border: none; padding: 6px 12px; }
        .segmented .source-option.selected {
          background: var(--card-background-color);
          box-shadow: 0 1px 2px color-mix(in srgb, var(--primary-text-color) 20%, transparent);
        }
        .bar strong { font-size: 13px; }
        .bar.off {
          background: color-mix(in srgb, var(--c) 22%, var(--card-background-color));
          color: var(--primary-text-color);
          box-shadow: inset 0 0 0 1.5px var(--c);
        }
        .bar.clash { box-shadow: inset 0 0 0 2px var(--error-color); }
        .bar.ghost { opacity: 0.55; pointer-events: none; }
        .handle { position: absolute; left: 0; right: 0; height: 12px; cursor: ns-resize; }
        .handle.start { top: 0; }
        .handle.end { bottom: 0; }
        .handle::after {
          content: "";
          position: absolute;
          left: 50%;
          width: 28px;
          height: 3px;
          margin-left: -14px;
          border-radius: 2px;
          background: currentColor;
          opacity: 0.7;
        }
        .handle.start::after { top: 4px; }
        .handle.end::after { bottom: 4px; }
        .now {
          position: absolute;
          left: 0;
          right: 0;
          height: 2px;
          margin-top: -1px;
          background: var(--primary-text-color);
          opacity: 0.45;
          pointer-events: none;
          z-index: 3;
        }
        .saving {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 12px;
          background: color-mix(in srgb, var(--card-background-color) 55%, transparent);
          font-weight: 600;
          z-index: 4;
        }
        .rows { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
        .row { display: flex; align-items: center; gap: 10px; min-height: 36px; }
        .swatch { width: 10px; height: 10px; border-radius: 99px; background: var(--c); flex: none; }
        .swatch.off { background: transparent; box-shadow: inset 0 0 0 1.5px var(--c); }
        .when { font-variant-numeric: tabular-nums; font-weight: 600; }
        .name { color: var(--secondary-text-color); font-size: 13px; }
        .spacer { flex: 1; }
        button.row-icon {
          width: 32px;
          height: 32px;
          border: none;
          border-radius: 999px;
          padding: 0;
          display: grid;
          place-items: center;
          background: var(--secondary-background-color, rgba(127,127,127,0.15));
          color: var(--primary-text-color);
          cursor: pointer;
        }
        button.row-icon svg { width: 18px; height: 18px; display: block; }
        button.row-icon.warn { color: var(--error-color); }
        button.row-icon:disabled { opacity: 0.45; cursor: default; }
        .error { margin: 10px 2px 0; color: var(--error-color); font-size: 12px; line-height: 1.4; }
      </style>
      <ha-card>
        <div class="head">
          <div>
            <div class="title"></div>
            <div class="sub"></div>
          </div>
          <div class="actions" hidden>
            <button class="icon discard" type="button" aria-label="Discard">
              <svg viewBox="0 0 24 24"><path fill="currentColor" d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
            </button>
            <button class="icon apply" type="button" aria-label="Apply">
              <svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
            </button>
          </div>
        </div>
        <div class="controls">
          <div class="kinds"></div>
          <div class="charge-source" hidden></div>
        </div>
        <div class="policy" hidden></div>
        <div class="day">
          <div class="hours"></div>
          <div class="track"></div>
        </div>
        <div class="error"></div>
      </ha-card>
    `;
    this._title = this._root.querySelector(".title");
    this._sub = this._root.querySelector(".sub");
    this._actions = this._root.querySelector(".actions");
    this._kinds = this._root.querySelector(".kinds");
    this._source = this._root.querySelector(".charge-source");
    this._policy = this._root.querySelector(".policy");
    this._hours = this._root.querySelector(".hours");
    this._track = this._root.querySelector(".track");
    this._error = this._root.querySelector(".error");
    this._source.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-ac]");
      if (button && !this._busy) this._stageAcCharge(button.dataset.ac === "on");
    });
    this._policy.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-limit]");
      if (!button || this._busy) return;
      this._stageLimit(button.dataset.limit, Number(button.dataset.step));
    });
    this._hours.innerHTML = Array.from({ length: 13 }, (_, step) => step * 2)
      .map((hour) => `<span style="top:${(hour / 24) * 100}%">${String(hour).padStart(2, "0")}:00</span>`)
      .join("");
    this._apply = this._root.querySelector(".apply");
    this._discard = this._root.querySelector(".discard");
    this._apply.addEventListener("click", () => this._applyChanges());
    this._discard.addEventListener("click", () => this._discardChanges());
    this._kinds.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-kind]");
      if (!button || this._busy) return;
      this._addWindow(button.dataset.kind);
    });
    this._track.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-act]");
      if (button) this._rowAction(button.dataset.act, button.dataset.id);
    });
    this._track.addEventListener("pointerdown", (event) => this._pointerDown(event));
    this._track.addEventListener("pointermove", (event) => this._pointerMove(event));
    this._track.addEventListener("pointerup", (event) => this._pointerUp(event));
    this._track.addEventListener("pointercancel", () => this._cancelDrag());
    this._paint();
  }

  _signature() {
    const windows = (this._config?.kinds || [])
      .flatMap((kind) => kind.periods)
      .map((period) =>
        [period.enable, period.start, period.end]
          .map((id) => this._hass?.states[id]?.state ?? "unavailable")
          .join(","),
      )
      .join("|");
    const settings = [
      this._config?.charge_from_grid,
      ...(this._config?.kinds || []).flatMap((kind) => [kind.power, kind.stop]),
    ]
      .map((id) => (id ? this._hass?.states[id]?.state ?? "unavailable" : "-"))
      .join(",");
    const sun = this._hass?.states?.["sun.sun"]?.attributes || {};
    const place = [
      this._config?.latitude,
      this._config?.longitude,
      this._config?.location?.latitude,
      this._config?.location?.longitude,
      this._hass?.config?.latitude,
      this._hass?.config?.longitude,
      sun.next_rising,
      sun.next_setting,
    ].join(",");
    return `${windows}|${settings}|${place}`;
  }

  _mode(kind) {
    return kind.mode || (kind.key === "charge" ? "charge" : "discharge");
  }

  _gridCharging() {
    const state = this._hass?.states[this._config.charge_from_grid]?.state;
    if (state !== "on" && state !== "off") return null;
    return this._acDraft ?? state === "on";
  }

  _setting(entityId) {
    const entity = this._hass?.states[entityId];
    if (!entity) return null;
    const value = Number(this._limits?.[entityId] ?? entity.state);
    return Number.isFinite(value) ? value : null;
  }

  _stageLimit(entityId, delta) {
    const entity = this._hass?.states[entityId];
    const live = Number(entity?.state);
    const current = this._setting(entityId);
    if (!entity || !Number.isFinite(live) || current === null || !Number.isFinite(delta)) return;
    const min = Number(entity.attributes.min ?? 0);
    const max = Number(entity.attributes.max ?? 100);
    const step = Number(entity.attributes.step ?? 1) || 1;
    const value = Math.min(max, Math.max(min, Math.round((current + delta) / step) * step));
    if (value === live) delete this._limits[entityId];
    else this._limits[entityId] = value;
    this._message = "";
    this._paint();
  }

  _daylight() {
    const now = new Date();
    const sun = this._hass?.states?.["sun.sun"]?.attributes || {};
    const key = [
      now.toDateString(),
      this._config.latitude,
      this._config.longitude,
      this._config.location?.latitude,
      this._config.location?.longitude,
      this._hass?.config?.latitude,
      this._hass?.config?.longitude,
      sun.next_rising,
      sun.next_setting,
    ].join("|");
    if (key !== this._daylightKey) {
      this._daylightKey = key;
      this._daylightCurve = resolveDaylight(this._config, this._hass, now);
    }
    return this._daylightCurve;
  }

  _explain(win) {
    const kind = win.kind;
    if (this._mode(kind) === "discharge") {
      const stop = this._setting(kind.stop);
      const power = this._setting(kind.power);
      if (stop !== null && stop >= 100) return SCHEDULE_PHRASES.discharge.blocked;
      if (power === 0) return SCHEDULE_PHRASES.discharge.idle;
    }
    const when = daylightSpan(win.start, win.end, this._daylight().curve);
    if (this._mode(kind) === "charge") {
      const grid = this._gridCharging();
      return SCHEDULE_PHRASES.charge[when][grid === null ? "unknown" : String(grid)];
    }
    return SCHEDULE_PHRASES.discharge[when];
  }

  _windows() {
    const windows = [];
    for (const kind of this._config.kinds) {
      kind.periods.forEach((period, index) => {
        const win = windowState(this._hass, period, index);
        win.kind = kind;
        win.id = `${kind.key}:${index}`;
        win.name = `${kind.label} ${index + 1}`;
        win.live = { start: win.start, end: win.end, enabled: win.enabled };
        const pending = this._draft[win.id];
        if (pending) {
          win.start = pending.start;
          win.end = pending.end;
          win.enabled = pending.enabled;
          win.empty = win.known && !win.enabled && win.start === 0 && win.end === 0;
          win.shown = win.known && !win.empty;
        }
        if (this._preview?.id === win.id) {
          win.start = this._preview.start;
          win.end = this._preview.end;
        }
        windows.push(win);
      });
    }
    return windows;
  }

  _paint() {
    if (!this._track || !this._config) return;
    const windows = this._windows();
    const shown = windows.filter((win) => win.shown);
    const lanes = Math.max(layout(shown), 1);

    const pending =
      Object.keys(this._draft).length +
      (this._acDraft === null ? 0 : 1) +
      Object.keys(this._limits).length;
    this._title.textContent = this._config.title;
    this._sub.textContent = this._busy
      ? "Saving to the inverter…"
      : pending
        ? pending === 1
          ? "1 unsaved change"
          : `${pending} unsaved changes`
        : `${shown.filter((win) => win.enabled).length} active windows`;
    this._actions.hidden = pending === 0;
    this._apply.disabled = this._busy;
    this._discard.disabled = this._busy;

    this._kinds.innerHTML = this._config.kinds
      .map((entry) => {
        const used = shown.filter((win) => win.kind.key === entry.key).length;
        const full = !windows.some((win) => win.kind.key === entry.key && win.empty);
        return `<button class="kind" type="button" data-kind="${escapeHtml(entry.key)}" style="--c:${escapeHtml(entry.color)}" ${this._busy || full ? "disabled" : ""}>
          <span class="dot"></span>${escapeHtml(entry.add_label || `Schedule a ${entry.label.toLowerCase()}`)}<span class="count">${used}/${entry.periods.length}</span>
        </button>`;
      })
      .join("");

    const grid = this._gridCharging();
    this._source.hidden =
      !this._config.charge_from_grid ||
      !this._config.kinds.some((entry) => this._mode(entry) === "charge");
    this._source.innerHTML = this._source.hidden
      ? ""
      : `<span class="label">Charge from</span><div class="segmented">
          <button class="source-option ${grid === false ? "selected" : ""}" type="button" data-ac="off" ${this._busy || grid === null ? "disabled" : ""}>Solar only</button>
          <button class="source-option ${grid === true ? "selected" : ""}" type="button" data-ac="on" ${this._busy || grid === null ? "disabled" : ""}>Solar + grid</button>
        </div>`;

    const discharge = this._config.kinds.find((entry) => this._mode(entry) === "discharge");
    const power = discharge ? this._setting(discharge.power) : null;
    const stop = discharge ? this._setting(discharge.stop) : null;
    this._policy.hidden = !discharge?.power || !discharge?.stop || power === null || stop === null;
    if (!this._policy.hidden) {
      const note =
        stop >= 100
          ? "Nothing leaves the battery until this is below full."
          : power <= 0
            ? "Nothing leaves the battery while power is off."
            : "Covers the house up to this power. Anything left goes out.";
      const stepper = (entityId, label, value, down, up) => `
        <span class="stepper">
          <span class="label">${label}</span>
          <button type="button" data-limit="${escapeHtml(entityId)}" data-step="${down}" aria-label="Lower ${label.toLowerCase()}" ${this._busy ? "disabled" : ""}>−</button>
          <span class="value">${value}%</span>
          <button type="button" data-limit="${escapeHtml(entityId)}" data-step="${up}" aria-label="Raise ${label.toLowerCase()}" ${this._busy ? "disabled" : ""}>+</button>
        </span>`;
      this._policy.style.setProperty("--c", discharge.color);
      this._policy.innerHTML = `<div class="policy-row">
          <span class="dot"></span>
          <span class="policy-title">Discharge</span>
          ${stepper(discharge.power, "Power", power, -5, 5)}
          ${stepper(discharge.stop, "Until", stop, -5, 5)}
        </div>
        <div class="policy-note">${note}</div>`;
    }

    this._track.style.height = `${this._config.height}px`;
    const bars = [];
    for (const win of shown) {
      const conflict =
        this._preview?.id === win.id &&
        clash(win, windows.filter((other) => other.id !== win.id));
      const segments = this._segments(win);
      const longest = Math.max(...segments.map((segment) => segment.to - segment.from));
      let detailed = false;
      for (const segment of segments) {
        const details = !detailed && segment.to - segment.from === longest;
        if (details) detailed = true;
        bars.push(this._bar(win, segment, lanes, Boolean(conflict), details));
      }
    }
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const saving = this._busy ? `<div class="saving">Saving…</div>` : "";
    const curve = this._daylight().curve;
    const wash = daylightGradient(curve, 24);
    const line = daylightGradient(curve, 78);
    this._track.innerHTML =
      `<div class="daylight" style="background:${wash}"></div><div class="sunline" style="background:${line}"></div>` +
      bars.join("") +
      `<div class="now" style="top:${pctOf(nowMin)}"></div>` +
      saving;
    this._error.textContent = this._message || "";
  }

  _segments(win) {
    if (win.end > win.start) return [{ from: win.start, to: win.end, edgeStart: true, edgeEnd: true }];
    if (win.start === win.end) return [];
    return [
      { from: win.start, to: DAY, edgeStart: true, edgeEnd: false },
      { from: 0, to: win.end, edgeStart: false, edgeEnd: true },
    ];
  }

  _bar(win, segment, lanes, conflict, details) {
    const length = segment.to - segment.from;
    const handles = [
      segment.edgeStart ? `<div class="handle start" data-edge="start"></div>` : "",
      segment.edgeEnd ? `<div class="handle end" data-edge="end"></div>` : "",
    ].join("");
    const times = `${label(win.start)}–${label(win.end)}`;
    let body = "";
    if (details) {
      const disabled = this._busy ? "disabled" : "";
      const toggle = win.enabled ? "Turn off" : "Turn on";
      body = `<div class="copy"><strong>${times} · ${escapeHtml(win.name)}${win.enabled ? "" : " · off"}</strong><span class="phrase">${escapeHtml(this._explain(win))}</span></div><div class="bar-actions"><button class="bar-button" type="button" data-act="toggle" data-id="${escapeHtml(win.id)}" title="${toggle}" aria-label="${toggle}" ${disabled}>${win.enabled ? POWER_OFF_ICON : POWER_ON_ICON}</button><button class="bar-button warn" type="button" data-act="remove" data-id="${escapeHtml(win.id)}" title="Remove" aria-label="Remove" ${disabled}>${DELETE_ICON}</button></div>`;
    } else if (length >= 25) {
      body = `<span>${times} · ${escapeHtml(win.kind.label)}</span>`;
    }
    const width = 100 / lanes;
    const classes = ["bar", win.enabled ? "" : "off", conflict ? "clash" : ""].join(" ");
    return `<div class="${classes}" data-id="${escapeHtml(win.id)}" style="--c:${escapeHtml(win.kind.color)};top:${pctOf(segment.from)};height:${pctOf(length)};left:calc(${win.lane * width}% + 4px);width:calc(${width}% - 8px)">${handles}${body}</div>`;
  }

  _minute(event) {
    const rect = this._track.getBoundingClientRect();
    const ratio = rect.height ? (event.clientY - rect.top) / rect.height : 0;
    return Math.max(0, Math.min(DAY - 1, Math.round(ratio * DAY)));
  }

  _pointerDown(event) {
    if (this._busy || event.button > 0 || event.target.closest?.("button")) return;
    const minute = this._minute(event);
    const target = event.target.closest?.(".bar");
    if (target?.dataset.id) {
      const win = this._windows().find((entry) => entry.id === target.dataset.id);
      if (!win) return;
      const edge = event.target.dataset?.edge || null;
      this._drag = {
        type: edge ? "resize" : "move",
        edge,
        id: win.id,
        origin: minute,
        start: win.start,
        end: win.end,
      };
      this._message = "";
      this._track.setPointerCapture(event.pointerId);
      this._paint();
    }
  }

  _pointerMove(event) {
    if (!this._drag || this._drag.type === "lock") return;
    const minute = this._minute(event);
    let next;
    if (this._drag.type === "move") {
      const delta = Math.round((minute - this._drag.origin) / SNAP) * SNAP;
      next = shift(this._drag.start, this._drag.end, delta);
    } else {
      next = resize(this._drag.start, this._drag.end, this._drag.edge, snap(minute));
    }
    const id = this._drag.id;
    this._preview = null;
    const windows = this._windows();
    const neighbours = windows.filter((win) => win.id !== id);
    const edge = this._drag.type === "move" ? null : this._drag.edge;
    let pulled = magnet(next, neighbours, edge);
    if (windows.find((win) => win.id === id)?.enabled) {
      pulled =
        settle(pulled, neighbours, edge) ||
        this._drag.last || { start: this._drag.start, end: this._drag.end };
    }
    this._drag.last = { start: pulled.start, end: pulled.end };
    this._preview = { id, ...pulled };
    this._paint();
  }

  _pointerUp(event) {
    if (!this._drag || this._drag.type === "lock") return;
    const drag = this._drag;
    this._drag = null;
    this._track.releasePointerCapture?.(event.pointerId);
    const next = this._preview;
    this._preview = null;
    const win = this._windows().find((entry) => entry.id === drag.id);
    if (!win || !next || (next.start === win.start && next.end === win.end)) {
      this._paint();
      return;
    }
    this._stage(win, next.start, next.end, win.enabled);
  }

  _cancelDrag() {
    if (this._drag?.type === "lock") return;
    this._drag = null;
    this._preview = null;
    this._paint();
  }

  _addWindow(key) {
    if (this._busy) return;
    const kind = this._config.kinds.find((entry) => entry.key === key);
    if (!kind) return;
    const windows = this._windows();
    const slot = windows.find((win) => win.kind.key === kind.key && win.empty);
    if (!slot) return;
    const placed = place(
      windows.filter((win) => win.shown),
      kind.suggestion || { start: "02:00:00", end: "04:00:00" },
    );
    if (!placed) {
      this._message = "There is no free time left for another window.";
      this._paint();
      return;
    }
    this._stage(slot, placed.start, placed.end, true);
  }

  _rowAction(action, id) {
    if (this._busy) return;
    const win = this._windows().find((entry) => entry.id === id);
    if (!win) return;
    if (action === "toggle") {
      this._stage(win, win.start, win.end, !win.enabled);
      return;
    }
    this._stage(win, 0, 0, false);
  }

  _discardChanges() {
    if (this._busy) return;
    this._draft = {};
    this._acDraft = null;
    this._limits = {};
    this._message = "";
    this._paint();
  }

  async _applyChanges() {
    const changes = this._windows()
      .filter((win) => this._draft[win.id])
      .map((win) => ({ key: win.id, ...this._draft[win.id], config: win.config, live: win.live }));
    const limits = Object.entries(this._limits);
    if ((!changes.length && this._acDraft === null && !limits.length) || this._busy) return;
    this._busy = true;
    this._message = "";
    this._drag = { type: "lock" };
    this._paint();
    try {
      await applyChanges(this._hass, changes, (change) => {
        delete this._draft[change.key];
      });
      if (this._acDraft !== null) {
        await this._hass.callService(
          "switch",
          this._acDraft ? "turn_on" : "turn_off",
          {},
          { entity_id: this._config.charge_from_grid },
        );
        this._acDraft = null;
      }
      for (const [entityId, value] of limits) {
        await this._hass.callService("number", "set_value", { value }, { entity_id: entityId });
        delete this._limits[entityId];
      }
    } catch (err) {
      this._message = message(err);
    } finally {
      this._busy = false;
      this._drag = null;
      this._signatureSeen = "";
      this._paint();
    }
  }

  _stageAcCharge(enabled) {
    const live = this._hass?.states[this._config.charge_from_grid]?.state === "on";
    this._acDraft = enabled === live ? null : enabled;
    this._message = "";
    this._paint();
  }

  _stage(win, start, end, enabled) {
    const others = this._windows().filter((other) => other.id !== win.id);
    const conflict = clash({ start, end, enabled }, others);
    if (conflict) {
      this._message = clashMessage(conflict);
      this._preview = null;
      this._paint();
      return;
    }
    const live = win.live;
    if (start === live.start && end === live.end && enabled === live.enabled) delete this._draft[win.id];
    else this._draft[win.id] = { start, end, enabled };
    this._message = "";
    this._preview = null;
    this._paint();
  }
}

function pctOf(minute) {
  return `${(minute / DAY) * 100}%`;
}

customElements.define("growatt-schedule-card", GrowattScheduleCard);
customElements.define("growatt-day-card", GrowattDayCard);
window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "growatt-schedule-card",
    name: "Growatt schedule",
    description: "A 24-hour editor for Growatt charge and grid-first windows.",
  },
  {
    type: "growatt-day-card",
    name: "Growatt day plan",
    description: "One vertical 24-hour column for every Growatt charge and grid-first window.",
  },
);
