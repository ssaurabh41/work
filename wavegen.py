#!/usr/bin/env python3
"""
wavegen.py - Interface timing waveform generator for IP / protocol documentation.

Reads a WaveDrom-compatible JSON description (``in.json`` by default) and renders a
self-contained, interactive HTML page (``out.html``) containing crisp SVG waveforms.

The waveform is a sequence of discrete steps ("bricks").  At each step a signal may
hold its level, take a rising edge, or take a falling edge.  Clock signals carry both
a rising and a falling edge inside a single step.

Wave language (WaveDrom compatible)
-----------------------------------
    0 1        low / high level
    l h        low / high level (clock-style alias)
    L H        low / high level, edge marked with an arrow
    p n        clock pulse, positive / negative polarity (two edges in one step)
    P N        clock pulse with the active edge marked
    u d        weak pull-up / pull-down (curved transition)
    z          high impedance (mid rail)
    x X        don't care (hatched)
    = 2..9     data / bus brick, 9 distinct colour slots
    .          repeat: extends a level or bus, or emits another clock pulse
    |          gap: draws a break symbol over the held value
    (space)    blank, nothing drawn

Extensions over stock WaveDrom
------------------------------
    config.theme        "dark" | "light"  - initial theme (toggleable in the page)
    config.hscale       horizontal scale factor
    config.vscale       vertical scale factor
    config.grid         draw the per-cycle grid
    config.clockArrows  "explicit" (default) | "all"
    config.title        page / diagram title
    config.subtitle     secondary caption
    marks[]             {from,to,label,color} bands or {at,label,color} cursors
    signal.color        per-signal stroke override
    signal.desc         tooltip shown on the signal name
    signal.period       stretch factor for one signal
    signal.phase        shift (in cycles) for one signal

Interactive page features: theme toggle, zoom, hover time cursor with cycle readout,
click-to-measure interval, signal search/dim, group collapse, and SVG export.

Usage
-----
    python3 wavegen.py                          # in.json -> out.html
    python3 wavegen.py -i axi.json -o axi.html
    python3 wavegen.py --theme light --hscale 1.5
    python3 wavegen.py --svg out.svg            # also emit a standalone SVG
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__version__ = "1.0.0"


# ===========================================================================
# Wave language tables
# ===========================================================================

CLOCK_POLARITY = {"p": "pos", "P": "pos", "n": "neg", "N": "neg",
                  "f": "pos", "F": "pos"}
CLOCK_MARKED = {"P", "N", "F"}
# f/F are p/P run fast: one step carries `cycles_per_unit` whole clock periods
# at 50% duty, rather than the single rise-and-fall of p/n.
FAST_CLOCK = {"f", "F"}
MAX_CYCLES_PER_UNIT = 32

LEVEL_RAIL = {
    "0": "low", "1": "high",
    "l": "low", "h": "high",
    "L": "low", "H": "high",
    "u": "high", "d": "low",
    "z": "mid",
}
LEVEL_MARKED = {"L", "H"}
LEVEL_WEAK = {"u", "d"}

DATA_CHARS = set("=23456789")
HATCH_CHARS = set("xX")
GAP_CHAR = "|"
REPEAT_CHAR = "."

# Bus colour slot: '=' is the neutral slot, '2'..'9' are the accent slots.
BUS_SLOT = {"=": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6, "8": 7, "9": 8}
BUS_SLOT_COUNT = 9


# ===========================================================================
# Geometry defaults (pixels, before hscale / vscale)
# ===========================================================================

CYCLE_W = 46.0        # width of one discrete step
ROW_H = 42.0          # vertical pitch of a signal row
WAVE_H = 24.0         # height of the wave band inside a row
SLEW = 3.2            # width of a level transition ramp
BUS_SLEW = 5.0        # width of a bus hexagon shoulder
NAME_W = 152.0        # width of the signal-name gutter
GROUP_W = 20.0        # gutter width consumed per nesting level
PAD_L = 12.0
PAD_R = 28.0
RULER_H = 26.0
MARK_LANE_H = 22.0    # lane above the ruler reserved for annotation-band labels
SPACER_H = 16.0
BUS_FONT = 12.0
NAME_FONT = 12.5


# ===========================================================================
# Theme tokens
# ===========================================================================

_BUS_DARK = [
    ("#242E42", "#4E617F", "#C8D6EE"),   # = neutral
    ("#262B5C", "#6B76DC", "#C2C9FF"),   # 2 indigo
    ("#123E39", "#2FA391", "#A2F0E1"),   # 3 teal
    ("#43331A", "#C99A3C", "#F5DEA6"),   # 4 amber
    ("#48212F", "#C86A88", "#FBC6D5"),   # 5 rose
    ("#0F3849", "#3A9AC1", "#ABE4F7"),   # 6 cyan
    ("#2A3D1A", "#7DAE48", "#D4EFAC"),   # 7 lime
    ("#482C16", "#CE7F3C", "#F8D1AB"),   # 8 orange
    ("#381E4F", "#9B62CE", "#DFC3F7"),   # 9 violet
]

_BUS_LIGHT = [
    ("#EEF2F8", "#A5B5CD", "#33405A"),
    ("#E7EAFF", "#7A85E8", "#2C348E"),
    ("#DAF5EF", "#3AA894", "#0F5449"),
    ("#FCF0D6", "#C4952F", "#694C0E"),
    ("#FDE4EB", "#D4778F", "#78273F"),
    ("#D8F0FA", "#469FC4", "#0E475C"),
    ("#E8F5D6", "#7BAE44", "#3C5316"),
    ("#FDEAD8", "#D6873F", "#77401A"),
    ("#EFE3FB", "#A272D4", "#4C2270"),
]

_MARK_DARK = {
    "indigo": "#6B76DC", "teal": "#2FA391", "amber": "#C99A3C",
    "rose": "#C86A88", "cyan": "#3A9AC1", "lime": "#7DAE48",
    "orange": "#CE7F3C", "violet": "#9B62CE", "slate": "#5C6B85",
}
_MARK_LIGHT = {
    "indigo": "#5A66D0", "teal": "#2A9282", "amber": "#B3861F",
    "rose": "#C4607B", "cyan": "#2E8CB2", "lime": "#679B33",
    "orange": "#C4732C", "violet": "#8B54C0", "slate": "#6C7C96",
}

# Print: the nine bus slots become a monotonic grey ramp rather than nine hues,
# so buses stay tellable apart in a black-and-white document.  Black outline and
# black text throughout; the darkest fill still carries black text legibly.
_BUS_PRINT = [
    (f, "#000000", "#000000") for f in (
        "#FFFFFF", "#F4F4F4", "#E9E9E9", "#DEDEDE", "#D3D3D3",
        "#C8C8C8", "#BDBDBD", "#B2B2B2", "#A7A7A7",
    )
]
_MARK_PRINT = {k: "#1A1A1A" for k in _MARK_DARK}


def theme_vars(name: str) -> dict[str, str]:
    """Return the CSS custom-property map for a theme."""
    if name == "print":
        # Formal black-and-white for embedding in a document: one ink colour,
        # a grey ramp for buses, and no tint between annotation markers.
        v = {
            "--wg-bg": "#FFFFFF",
            "--wg-panel": "#FFFFFF",
            "--wg-panel-2": "#F4F4F4",
            "--wg-border": "#BDBDBD",
            "--wg-text": "#000000",
            "--wg-muted": "#2E2E2E",
            "--wg-faint": "#5C5C5C",
            "--wg-accent": "#1A1A1A",
            "--wg-accent-soft": "#ECECEC",
            "--wg-wave": "#000000",
            "--wg-wave-clk": "#000000",
            "--wg-grid": "#EDEDED",
            "--wg-grid-strong": "#D2D2D2",
            "--wg-row-alt": "rgba(0, 0, 0, 0.025)",
            "--wg-row-hover": "rgba(0, 0, 0, 0.06)",
            "--wg-edge": "#1A1A1A",
            "--wg-edge-text": "#000000",
            "--wg-edge-chip": "#FFFFFF",
            "--wg-cursor": "#1A1A1A",
            "--wg-measure": "#1A1A1A",
            "--wg-fold": "#1A1A1A",
            "--wg-hatch": "#9A9A9A",
            "--wg-hatch-bg": "#FFFFFF",
            "--wg-shadow": "rgba(0, 0, 0, 0.12)",
            # The band between annotation markers carries no fill in print; the
            # dashed boundaries and the label still say where the section is.
            "--wg-markband-op": "0",
        }
        buses, marks = _BUS_PRINT, _MARK_PRINT
    elif name == "light":
        v = {
            "--wg-bg": "#FAFBFD",
            "--wg-panel": "#FFFFFF",
            "--wg-panel-2": "#F2F5FA",
            "--wg-border": "#E0E5EF",
            "--wg-text": "#1B2233",
            "--wg-muted": "#5F6B82",
            "--wg-faint": "#94A0B5",
            "--wg-accent": "#2E5FD0",
            "--wg-accent-soft": "#E5EDFC",
            "--wg-wave": "#2A3854",
            "--wg-wave-clk": "#1F7A8C",
            "--wg-grid": "#EBEFF6",
            "--wg-grid-strong": "#D8DFEA",
            "--wg-row-alt": "rgba(15, 25, 45, 0.022)",
            "--wg-row-hover": "rgba(46, 95, 208, 0.055)",
            "--wg-edge": "#B4701A",
            "--wg-edge-text": "#7A4B0E",
            "--wg-edge-chip": "#FCF2E0",
            "--wg-cursor": "#C9761A",
            "--wg-measure": "#2E5FD0",
            "--wg-fold": "#8A6FB8",
            "--wg-hatch": "#AAB6C9",
            "--wg-hatch-bg": "#F0F3F8",
            "--wg-shadow": "rgba(20, 30, 55, 0.10)",
            "--wg-markband-op": "0.075",
        }
        buses, marks = _BUS_LIGHT, _MARK_LIGHT
    else:
        v = {
            "--wg-bg": "#0B0E14",
            "--wg-panel": "#111621",
            "--wg-panel-2": "#161C29",
            "--wg-border": "#212938",
            "--wg-text": "#DCE3F0",
            "--wg-muted": "#8391A8",
            "--wg-faint": "#5C6A82",
            "--wg-accent": "#5B8DEF",
            "--wg-accent-soft": "#18243B",
            "--wg-wave": "#AFC1E0",
            "--wg-wave-clk": "#5FCBB8",
            "--wg-grid": "#171E2B",
            "--wg-grid-strong": "#232C3E",
            "--wg-row-alt": "rgba(255, 255, 255, 0.017)",
            "--wg-row-hover": "rgba(91, 141, 239, 0.085)",
            "--wg-edge": "#E0A458",
            "--wg-edge-text": "#F3D7A8",
            "--wg-edge-chip": "#2A2216",
            "--wg-cursor": "#F0C674",
            "--wg-measure": "#5B8DEF",
            "--wg-fold": "#A98BD8",
            "--wg-hatch": "#54627C",
            "--wg-hatch-bg": "#141A25",
            "--wg-shadow": "rgba(0, 0, 0, 0.45)",
            "--wg-markband-op": "0.075",
        }
        buses, marks = _BUS_DARK, _MARK_DARK

    for i, (fill, stroke, text) in enumerate(buses):
        v[f"--wg-b{i}-fill"] = fill
        v[f"--wg-b{i}-stroke"] = stroke
        v[f"--wg-b{i}-text"] = text
    for key, col in marks.items():
        v[f"--wg-mark-{key}"] = col
    return v


VAR_NAMES = sorted(theme_vars("dark").keys())


# ===========================================================================
# Wave parsing
# ===========================================================================

@dataclass
class Brick:
    """One discrete step (or a run of merged identical steps) of a waveform."""
    kind: str                       # clock | level | data | hatch | blank
    char: str
    start: float                    # start position, in cycles
    span: float                     # width, in cycles
    rail: str | None = None         # low | high | mid  (level bricks)
    polarity: str | None = None     # pos | neg         (clock bricks)
    text: str | None = None         # bus value         (data bricks)
    slot: int = 0                   # bus colour slot
    marked: bool = False            # draw an edge arrow
    weak: bool = False              # pull-up / pull-down
    subcycles: int = 1              # clock periods packed into this one step


@dataclass
class Wave:
    bricks: list[Brick] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)   # cycle positions of break symbols
    end: float = 0.0                                  # last occupied cycle


_REPEAT_RE = re.compile(r"([^{}])\{(\d+)\}")


def expand_repeats(wave: str) -> str:
    """Expand ``c{n}`` run-length syntax: the brick ``c`` spans n steps.

    ``"1{32}"`` is ``"1"`` followed by 31 dots, so a 32-cycle hold is written
    once rather than counted out by hand.  ``"P{24}"`` is 24 clock pulses with
    a single edge marker, and ``"={4}"`` is one bus brick four steps wide.
    ``{`` is not a wave character, so this cannot change an existing diagram.
    """
    def sub(m: re.Match) -> str:
        return m.group(1) + "." * max(int(m.group(2)) - 1, 0)
    prev = None
    # Loop so that "0.{3}" style chains collapse fully, but bound the work.
    for _ in range(8):
        if wave == prev:
            break
        prev, wave = wave, _REPEAT_RE.sub(sub, wave)
    return wave


def parse_wave(wave: str, data: Any, period: float = 1.0, phase: float = 0.0,
               clock_arrows: str = "explicit", cycles_per_unit: int = 1) -> Wave:
    """Expand a wave string into positioned bricks.

    ``period`` stretches every step; ``phase`` shifts the whole wave left.
    ``cycles_per_unit`` sets how many clock periods an ``f``/``F`` step holds.
    """
    cycles_per_unit = max(1, min(int(cycles_per_unit or 1), MAX_CYCLES_PER_UNIT))
    period = float(period or 1.0)
    if period <= 0:
        period = 1.0
    phase = float(phase or 0.0)

    if isinstance(data, str):
        values = data.split()
    elif isinstance(data, (list, tuple)):
        values = [str(d) for d in data]
    else:
        values = []

    out = Wave()
    pos = -phase
    di = 0
    prev_char: str | None = None

    for raw in expand_repeats(wave or ""):
        if raw == REPEAT_CHAR:
            ch, fresh = prev_char, False
        else:
            ch, fresh = raw, True

        # A leading '.' has nothing to repeat.
        if ch is None:
            out.bricks.append(Brick("blank", " ", pos, period))
            pos += period
            continue

        if raw == GAP_CHAR:
            # A gap holds the current value and stamps a break symbol on it.
            if out.bricks and out.bricks[-1].kind != "blank":
                out.bricks[-1].span += period
            else:
                out.bricks.append(Brick("blank", " ", pos, period))
            out.gaps.append(pos + period / 2.0)
            pos += period
            continue

        if ch in CLOCK_POLARITY:
            marked = (raw in CLOCK_MARKED) if clock_arrows == "explicit" else (ch in CLOCK_MARKED)
            out.bricks.append(Brick(
                "clock", ch, pos, period,
                polarity=CLOCK_POLARITY[ch], marked=marked,
                subcycles=cycles_per_unit if ch in FAST_CLOCK else 1))

        elif ch in DATA_CHARS or ch in HATCH_CHARS:
            kind = "hatch" if ch in HATCH_CHARS else "data"
            same = (out.bricks and out.bricks[-1].kind == kind
                    and out.bricks[-1].char == ch)
            if fresh or not same:
                text = None
                if kind == "data":
                    text = values[di] if di < len(values) else ""
                    di += 1
                out.bricks.append(Brick(
                    kind, ch, pos, period,
                    text=text, slot=BUS_SLOT.get(ch, 0)))
            else:
                out.bricks[-1].span += period

        elif ch in LEVEL_RAIL:
            rail = LEVEL_RAIL[ch]
            weak = ch in LEVEL_WEAK
            last = out.bricks[-1] if out.bricks else None
            if (last and last.kind == "level" and last.rail == rail
                    and last.weak == weak):
                last.span += period
            else:
                out.bricks.append(Brick(
                    "level", ch, pos, period,
                    rail=rail, marked=(raw in LEVEL_MARKED), weak=weak))

        else:                       # space or anything unrecognised
            out.bricks.append(Brick("blank", ch, pos, period))

        prev_char = ch
        pos += period

    out.end = pos
    return out


# ===========================================================================
# Document model
# ===========================================================================

@dataclass
class Row:
    kind: str                       # signal | spacer
    sig: dict
    depth: int
    index: int
    y: float = 0.0                  # top of the row
    height: float = 0.0
    wave: Wave | None = None


@dataclass
class Group:
    label: str
    depth: int
    first: int                      # first row index (inclusive)
    last: int                       # last row index (inclusive)


@dataclass
class Fold:
    """A span of dead time collapsed to a narrow break band."""
    a: float                        # first folded cycle
    b: float                        # first cycle after the fold
    label: str = ""
    force: bool = False             # render even if a signal toggles inside

    @property
    def cycles(self) -> float:
        return self.b - self.a

    def text(self) -> str:
        if self.label:
            return self.label
        n = self.cycles
        n = int(n) if float(n).is_integer() else n
        return f"{n} cycles"


def normalize_folds(spec: Any, total: float) -> list[Fold]:
    """Clamp, drop degenerate, sort, and merge overlapping folds."""
    raw: list[Fold] = []
    for item in spec or []:
        if not isinstance(item, dict):
            continue
        try:
            a = float(item.get("from", item.get("at", 0)))
            b = float(item.get("to", a))
        except (TypeError, ValueError):
            continue
        if "cycles" in item:                    # {"from": 10, "cycles": 32}
            b = a + float(item["cycles"])
        a, b = max(0.0, min(a, b)), min(total, max(a, b))
        if b - a <= 0:
            continue
        raw.append(Fold(a, b, str(item.get("label", "")), bool(item.get("force"))))

    raw.sort(key=lambda f: f.a)
    merged: list[Fold] = []
    for f in raw:
        if merged and f.a <= merged[-1].b:
            last = merged[-1]
            last.b = max(last.b, f.b)
            last.label = last.label or f.label
            last.force = last.force or f.force
        else:
            merged.append(f)
    return merged


def flatten(items: Any, depth: int = 0, rows: list[Row] | None = None,
            groups: list[Group] | None = None) -> tuple[list[Row], list[Group]]:
    """Flatten the nested WaveJSON ``signal`` tree into rows plus group spans."""
    rows = [] if rows is None else rows
    groups = [] if groups is None else groups

    for item in items or []:
        if isinstance(item, list):
            label = item[0] if item and isinstance(item[0], str) else ""
            children = item[1:] if (item and isinstance(item[0], str)) else item
            first = len(rows)
            flatten(children, depth + 1, rows, groups)
            if len(rows) > first:
                groups.append(Group(label, depth, first, len(rows) - 1))
        elif isinstance(item, dict):
            if item.get("index"):
                kind = "index"
            elif item.get("name") or item.get("wave"):
                kind = "signal"
            else:
                kind = "spacer"
            rows.append(Row(kind, item, depth, len(rows)))
        else:
            rows.append(Row("spacer", {}, depth, len(rows)))
    return rows, groups


class Diagram:
    """Parsed, laid-out diagram ready for rendering."""

    def __init__(self, doc: dict, overrides: dict | None = None):
        cfg = dict(doc.get("config") or {})
        cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})

        self.doc = doc
        self.config = cfg
        self.theme = cfg.get("theme", "dark")
        if self.theme not in ("dark", "light", "print"):
            self.theme = "dark"

        self.hscale = float(cfg.get("hscale", 1) or 1)
        self.vscale = float(cfg.get("vscale", 1) or 1)
        self.show_grid = bool(cfg.get("grid", True))
        self.clock_arrows = cfg.get("clockArrows", "explicit")
        # How many clock periods an f/F step holds. 1 makes f identical to p.
        self.cycles_per_unit = max(1, min(
            int(cfg.get("cycles_per_unit", cfg.get("cyclesPerUnit", 1)) or 1),
            MAX_CYCLES_PER_UNIT))

        self.head = _as_block(doc.get("head"))
        self.foot = _as_block(doc.get("foot"))
        self.title = cfg.get("title") or self.head.get("text") or "Timing Diagram"
        self.subtitle = cfg.get("subtitle") or ""

        self.marks = list(doc.get("marks") or [])
        self.edges = list(doc.get("edge") or doc.get("edges") or [])
        self.rows, self.groups = flatten(doc.get("signal"))

        # Parse every wave, and find the widest.
        self.cycles = 0.0
        for row in self.rows:
            if row.kind != "signal":
                continue
            row.wave = parse_wave(
                row.sig.get("wave", ""),
                row.sig.get("data"),
                row.sig.get("period", 1),
                row.sig.get("phase", 0),
                self.clock_arrows,
                # A signal may override the document rate for its own clock.
                row.sig.get("cycles_per_unit",
                            row.sig.get("cyclesPerUnit", self.cycles_per_unit)),
            )
            self.cycles = max(self.cycles, row.wave.end)
        self.cycles = max(self.cycles, 1.0)

        # Folded (dead-time) spans.  The time axis becomes piecewise: folded
        # cycles collapse to a fixed narrow band, everything else is linear.
        self.fold_w = float(cfg.get("foldWidth", 0.34) or 0.34)
        self.folds = normalize_folds(doc.get("folds"), self.cycles)
        self._check_folds()
        self.vcycles = self._u(self.cycles)      # width in *visible* cycle units

        # Geometry
        self.cw = CYCLE_W * self.hscale
        self.row_h = ROW_H * self.vscale
        self.wave_h = min(WAVE_H * self.vscale, self.row_h - 12)
        self.max_depth = max([g.depth + 1 for g in self.groups], default=0)
        self.gutter = self.max_depth * GROUP_W
        self.name_w = PAD_L + self.gutter + NAME_W

        # Vertical layout.  Annotation bands get their own lane above the ruler so
        # their labels never collide with the cycle numbers.
        self.mark_lane = MARK_LANE_H if (self.marks or self.folds) else 0.0
        self.ruler_y = self.mark_lane
        # A step-index row numbers every step itself, so the ruler drops its
        # numbers and shrinks to bare tick marks rather than repeating them.
        self.has_index = any(r.kind == "index" for r in self.rows)
        self.ruler_h = 12.0 if self.has_index else RULER_H
        y = self.ruler_y + self.ruler_h + 10
        for row in self.rows:
            row.height = (self.row_h if row.kind in ("signal", "index")
                          else SPACER_H * self.vscale)
            row.y = y
            y += row.height
        self.rows_bottom = y
        self.height = y + (24 if self.edges else 14)
        self.width = self.name_w + self.vcycles * self.cw + PAD_R

        self.nodes = self._collect_nodes()

    # -- helpers ---------------------------------------------------------

    def row_rails(self, row: Row) -> tuple[float, float, float]:
        """Return (high_y, low_y, mid_y) for a signal row."""
        hi = row.y + (row.height - self.wave_h) / 2.0
        lo = hi + self.wave_h
        return hi, lo, (hi + lo) / 2.0

    def _u(self, cycle: float) -> float:
        """Real cycle -> visible cycle units, collapsing folded spans."""
        u = 0.0
        prev = 0.0
        for f in self.folds:
            if cycle <= f.a:
                break
            u += f.a - prev
            if cycle < f.b:                      # inside the band: interpolate
                return u + self.fold_w * (cycle - f.a) / f.cycles
            u += self.fold_w
            prev = f.b
        return u + cycle - prev

    def x_of(self, cycle: float) -> float:
        """Cycle position -> x inside the wave group (gutter already stripped)."""
        return self._u(cycle) * self.cw

    def in_fold(self, cycle: float) -> bool:
        """True when a cycle falls strictly inside a folded span."""
        return any(f.a < cycle < f.b for f in self.folds)

    def _check_folds(self) -> None:
        """Refuse to hide a real transition inside a fold.

        A clock toggles every cycle, so clock-only rows never count -- eliding
        them is the whole point.  Anything else changing state inside a fold
        would be silently lost, which is a bug in the diagram, not a style.
        """
        bad: list[str] = []
        for f in self.folds:
            if f.force:
                continue
            for row in self.rows:
                if row.kind != "signal" or not row.wave or not row.wave.bricks:
                    continue
                if all(b.kind in ("clock", "blank") for b in row.wave.bricks):
                    continue
                for br in row.wave.bricks:
                    for edge in (br.start, br.start + br.span):
                        if f.a < edge < f.b:
                            bad.append(
                                f'  {row.sig.get("name", "?")} changes at cycle '
                                f'{edge:g}, inside fold {f.a:g}-{f.b:g}')
        if bad:
            raise SystemExit(
                "wavegen: folds would hide real transitions:\n"
                + "\n".join(sorted(set(bad))[:12])
                + '\nMove the fold, or set "force": true on it to render anyway.')

    def _collect_nodes(self) -> dict[str, tuple[float, float]]:
        nodes: dict[str, tuple[float, float]] = {}
        for row in self.rows:
            if row.kind != "signal":
                continue
            spec = row.sig.get("node")
            if not spec:
                continue
            period = float(row.sig.get("period", 1) or 1)
            phase = float(row.sig.get("phase", 0) or 0)
            _, _, mid = self.row_rails(row)
            for i, ch in enumerate(expand_repeats(spec)):
                if ch in ".| ":
                    continue
                nodes[ch] = (self.x_of(i * period - phase), mid)
        return nodes


def _as_block(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"text": value}
    return {}


# ===========================================================================
# SVG rendering
# ===========================================================================

def esc(value: Any) -> str:
    return _html.escape(str(value), quote=True)


def fmt(n: float) -> str:
    """Compact number formatting for path data."""
    return f"{n:.2f}".rstrip("0").rstrip(".")


def _rail_y(rail: str, hi: float, lo: float, mid: float) -> float:
    return {"high": hi, "low": lo, "mid": mid}[rail]


def _entry_y(b: Brick, hi: float, lo: float, mid: float) -> float | None:
    if b.kind == "clock":
        return hi if b.polarity == "pos" else lo
    if b.kind == "level":
        return _rail_y(b.rail, hi, lo, mid)
    return None


def _exit_y(b: Brick, hi: float, lo: float, mid: float) -> float | None:
    if b.kind == "clock":
        return lo if b.polarity == "pos" else hi
    if b.kind == "level":
        return _rail_y(b.rail, hi, lo, mid)
    return None


def _is_bus(b: Brick) -> bool:
    return b.kind in ("data", "hatch")


def render_row_wave(dg: Diagram, row: Row) -> str:
    """Render one signal's wave: bus shapes, rail path, edge arrows, gaps."""
    wave = row.wave
    if wave is None or not wave.bricks:
        return ""

    hi, lo, mid = dg.row_rails(row)
    base_slew = min(SLEW, dg.cw / 3.0)
    bricks = wave.bricks
    n = len(bricks)

    def X(cycle: float) -> float:
        return dg.x_of(cycle)

    # Junction rail height at the left boundary of each brick.  Buses meet other
    # buses at mid-rail (forming the classic crossover) and meet levels at that
    # level's height so the bus visibly opens out of / closes into the line.
    junction: list[float] = []
    for i, b in enumerate(bricks):
        prev = bricks[i - 1] if i > 0 else None
        if prev is None or prev.kind == "blank":
            junction.append(mid)
        elif _is_bus(prev):
            entry = _entry_y(b, hi, lo, mid)
            junction.append(entry if entry is not None else mid)
        else:
            junction.append(_exit_y(prev, hi, lo, mid) or mid)
    # One extra junction for the right edge of the last brick.
    last = bricks[-1]
    junction.append(mid if _is_bus(last) else (_exit_y(last, hi, lo, mid) or mid))

    parts: list[str] = []
    rail_paths: list[str] = []
    cmds: list[str] = []
    prev_exit: float | None = None
    prev_bus = False

    stroke_style = ""
    if row.sig.get("color"):
        stroke_style = f' style="stroke:{esc(row.sig["color"])}"'

    def flush() -> None:
        nonlocal cmds
        if len(cmds) > 1:
            rail_paths.append(" ".join(cmds))
        cmds = []

    for i, b in enumerate(bricks):
        xs, xe = X(b.start), X(b.start + b.span)
        width = xe - xs

        if b.kind == "blank":
            flush()
            prev_exit, prev_bus = None, False
            continue

        if _is_bus(b):
            flush()
            bs = max(1.0, min(BUS_SLEW, width / 2.0 - 0.5))
            ly = junction[i] if not prev_bus else mid
            ry = junction[i + 1]
            body = (
                f"M {fmt(xs)} {fmt(ly)} L {fmt(xs + bs)} {fmt(hi)} "
                f"L {fmt(xe - bs)} {fmt(hi)} L {fmt(xe)} {fmt(ry)} "
                f"L {fmt(xe - bs)} {fmt(lo)} L {fmt(xs + bs)} {fmt(lo)} Z"
            )
            if b.kind == "hatch":
                parts.append(f'<path class="wg-hatchbox" d="{body}"/>')
                parts.append(f'<path class="wg-hatchfill" d="{body}"/>')
            else:
                cls = f"wg-bus wg-b{b.slot}"
                parts.append(f'<path class="{cls}" d="{body}"/>')
                full = b.text or ""
                avail = width - 2 * bs - 6
                label = _fit_text(full, avail)
                if full:
                    cx = (xs + xe) / 2.0
                    # data-avail is the room for text at scale 1; the page rescales
                    # it by the live time-unit factor and re-truncates.
                    parts.append(
                        f'<text class="wg-bustext wg-nox wg-b{b.slot}-t" '
                        f'x="{fmt(cx)}" y="{fmt(mid)}" dy="0.34em" '
                        f'data-ax="{fmt(cx)}" data-avail="{fmt(avail)}" '
                        f'data-full="{esc(full)}">{esc(label)}'
                        f'<title>{esc(full)}</title></text>'
                    )
            prev_exit, prev_bus = None, True
            continue

        # --- single-rail bricks (levels and clock pulses) ---
        entry = _entry_y(b, hi, lo, mid)
        exit_y = _exit_y(b, hi, lo, mid)

        # A fast clock packs several periods into one step, so the transition
        # ramp has to shrink with them or it swallows the pulse whole.
        sub = width / max(1, b.subcycles)
        slew = min(base_slew, sub / 6.0)

        if prev_bus:
            # The preceding bus already closed onto this brick's entry height.
            cmds = [f"M {fmt(xs)} {fmt(entry)}"]
        elif prev_exit is None:
            cmds = [f"M {fmt(xs)} {fmt(entry)}"]
        elif prev_exit != entry:
            if b.weak:
                # Pull-up / pull-down: ease into the new rail.
                cmds.append(f"L {fmt(xs)} {fmt(prev_exit)}")
                cmds.append(
                    f"Q {fmt(xs + slew * 1.6)} {fmt(prev_exit)} "
                    f"{fmt(xs + slew * 2.4)} {fmt(entry)}"
                )
            else:
                cmds.append(f"L {fmt(max(xs - slew / 2, 0))} {fmt(prev_exit)}")
                cmds.append(f"L {fmt(xs + slew / 2)} {fmt(entry)}")
            if b.marked:
                parts.append(_edge_arrow(xs, prev_exit, entry, hi, lo))
        else:
            cmds.append(f"L {fmt(xs)} {fmt(entry)}")

        if b.kind == "clock":
            # One period per subcycle at 50% duty: hold `entry` for the first
            # half, `exit_y` for the second, then rise again for the next.
            for i in range(max(1, b.subcycles)):
                xa = xs + i * sub
                xm = xa + sub / 2.0
                if i:                       # rising edge into this period
                    cmds.append(f"L {fmt(xa - slew / 2)} {fmt(exit_y)}")
                    cmds.append(f"L {fmt(xa + slew / 2)} {fmt(entry)}")
                cmds.append(f"L {fmt(xm - slew / 2)} {fmt(entry)}")
                cmds.append(f"L {fmt(xm + slew / 2)} {fmt(exit_y)}")
            cmds.append(f"L {fmt(xe)} {fmt(exit_y)}")
            if b.marked:
                # Mark the active edge: the step's own leading edge at xs.  One
                # arrow per step -- a fast clock would otherwise be all arrows.
                parts.append(_edge_arrow(xs, exit_y, entry, hi, lo))

        else:
            cmds.append(f"L {fmt(xe)} {fmt(exit_y)}")

        prev_exit, prev_bus = exit_y, False

    flush()

    clk = " wg-clk" if any(b.kind == "clock" for b in bricks) else ""
    for d in rail_paths:
        parts.append(f'<path class="wg-rail{clk}" d="{d}"{stroke_style}/>')

    for gx in wave.gaps:
        parts.append(_gap_symbol(X(gx), hi, lo))

    return "".join(parts)


def _fit_text(text: str, avail: float) -> str:
    """Truncate a bus label to the space available."""
    if not text:
        return ""
    per = BUS_FONT * 0.60
    limit = int(max(0.0, avail) // per)
    if len(text) <= limit:
        return text
    # Below two characters an ellipsis alone is just noise — the brick shape
    # already says a value is there, and the tooltip still carries it.
    if limit < 2:
        return ""
    return text[: limit - 1] + "…"


def _edge_arrow(x: float, from_y: float, to_y: float, hi: float, lo: float) -> str:
    """Small triangle marking an active clock / level edge."""
    up = to_y < from_y
    h = (lo - hi)
    cy = hi + h * (0.34 if up else 0.66)
    w, t = 3.4, 4.6
    if up:
        d = (f"M {fmt(x)} {fmt(cy - t / 2)} L {fmt(x - w)} {fmt(cy + t / 2)} "
             f"L {fmt(x + w)} {fmt(cy + t / 2)} Z")
    else:
        d = (f"M {fmt(x)} {fmt(cy + t / 2)} L {fmt(x - w)} {fmt(cy - t / 2)} "
             f"L {fmt(x + w)} {fmt(cy - t / 2)} Z")
    return f'<path class="wg-edgemark wg-nox" data-ax="{fmt(x)}" d="{d}"/>'


def _gap_symbol(x: float, hi: float, lo: float) -> str:
    """Zig-zag break drawn over a held value."""
    top, bot = hi - 4, lo + 4
    h = bot - top
    curve = (lambda ox:
             f"M {fmt(x + ox + 2)} {fmt(bot)} "
             f"C {fmt(x + ox - 6)} {fmt(bot - h * 0.30)} "
             f"{fmt(x + ox + 10)} {fmt(bot - h * 0.70)} "
             f"{fmt(x + ox + 2)} {fmt(top)}")
    return (
        f'<g class="wg-nox" data-ax="{fmt(x)}">'
        f'<rect class="wg-gapfill" x="{fmt(x - 4)}" y="{fmt(top)}" '
        f'width="9" height="{fmt(h)}"/>'
        f'<path class="wg-gapline" d="{curve(-4)}"/>'
        f'<path class="wg-gapline" d="{curve(3)}"/>'
        f'</g>'
    )


# --------------------------------------------------------------------------
# Ruler, grid, marks, groups, names, edges
# --------------------------------------------------------------------------

def render_ruler(dg: Diagram) -> str:
    head = dg.head
    total = int(dg.cycles + 0.999)
    tick = head.get("tick")
    tock = head.get("tock")
    every = int(head.get("every", 1) or 1)

    visible = sum(1 for c in range(total) if not dg.in_fold(c))
    if tick is None and tock is None:
        base, centred = 0, True
        every = 1 if visible <= 40 else (2 if visible <= 80 else 5)
    elif tock is not None:
        base, centred = int(tock), True
    else:
        base, centred = int(tick), False

    out = [f'<rect class="wg-rulerbg" id="wg-rulerbg" x="0" y="0" '
           f'width="{fmt(dg.vcycles * dg.cw)}" height="{fmt(dg.ruler_h)}"/>']
    y = dg.ruler_h - 8
    # Every tick is emitted with its cycle index so the page can re-thin them as
    # the time unit changes; `every` only decides what is visible at base scale.
    for c in range(total + (0 if centred else 1)):
        # A folded cycle has no room on the axis; its number is elided and the
        # ruler jumps, which is what tells the reader time was skipped.
        if dg.in_fold(c + (0.5 if centred else 0.0)) or dg.in_fold(c):
            continue
        x = dg.x_of(c + (0.5 if centred else 0.0))
        strong = (c % every == 0)
        if not dg.has_index:
            hide = "" if strong else ' style="display:none"'
            out.append(
                f'<text class="wg-tick wg-nox" x="{fmt(x)}" y="{fmt(y)}" '
                f'data-ax="{fmt(x)}" data-cyc="{c}"{hide}>{base + c}</text>')
        out.append(
            f'<line class="wg-tickmark{"" if strong else " wg-faintline"}" '
            f'x1="{fmt(dg.x_of(c))}" y1="{fmt(dg.ruler_h - 5)}" '
            f'x2="{fmt(dg.x_of(c))}" y2="{fmt(dg.ruler_h)}"/>')
    return "".join(out)


def render_grid(dg: Diagram) -> str:
    if not dg.show_grid:
        return ""
    out = []
    y0, y1 = dg.ruler_y + dg.ruler_h, dg.rows_bottom
    total = int(dg.cycles + 0.999)
    for c in range(total + 1):
        if dg.in_fold(c):
            continue
        x = dg.x_of(c)
        cls = "wg-gridline" + (" wg-gridline-strong" if c % 5 == 0 else "")
        out.append(f'<line class="{cls}" x1="{fmt(x)}" y1="{fmt(y0)}" '
                   f'x2="{fmt(x)}" y2="{fmt(y1)}"/>')
    return "".join(out)


def render_index_row(dg: Diagram, row: Row) -> str:
    """A virtual row numbering every step: 0, 1, 2 ...

    Declared with ``{"name": "step", "index": true}``.  Purely for readability:
    it makes the step count legible next to the signals instead of only at the
    top ruler, and its numbers jump across a fold so the elided span is obvious.
    """
    opt = row.sig.get("index")
    opt = opt if isinstance(opt, dict) else {}
    start = int(opt.get("from", 0))
    every = max(1, int(opt.get("every", 1)))

    hi, lo, mid = dg.row_rails(row)
    h = lo - hi
    out = []
    for c in range(int(dg.cycles + 0.999)):
        if dg.in_fold(c + 0.5):
            continue
        xs, xe = dg.x_of(c), dg.x_of(c + 1)
        out.append(
            f'<rect class="wg-idxcell{" wg-idxalt" if c % 2 else ""}" '
            f'x="{fmt(xs)}" y="{fmt(hi)}" width="{fmt(max(xe - xs, 0))}" '
            f'height="{fmt(h)}" rx="2"/>')
        if c % every == 0:
            cx = (xs + xe) / 2.0
            out.append(
                f'<text class="wg-idxtext wg-nox" x="{fmt(cx)}" y="{fmt(mid)}" '
                f'dy="0.34em" data-ax="{fmt(cx)}" data-idxw="{fmt(xe - xs)}">'
                f'{start + c}</text>')
    return "".join(out)


def render_folds(dg: Diagram) -> str:
    """Break bands over the folded spans, drawn on top of the waves.

    The band is painted with the page ground and edged with two torn lines, so
    every trace passing under it reads as held-but-elided rather than changed.
    """
    if not dg.folds:
        return ""
    out = []
    y0, y1 = dg.ruler_y, dg.rows_bottom
    h = y1 - y0
    for f in dg.folds:
        xa, xb = dg.x_of(f.a), dg.x_of(f.b)
        out.append(f'<rect class="wg-foldband" x="{fmt(xa)}" y="{fmt(y0)}" '
                   f'width="{fmt(xb - xa)}" height="{fmt(h)}"/>')
        for x in (xa, xb):
            out.append(f'<path class="wg-foldedge" d="{_torn_line(x, y0, y1)}"/>')
        out.append(_chip((xa + xb) / 2.0, y0 - 5, f.text(),
                         "var(--wg-fold)", anchor="middle"))
    return "".join(out)


def _torn_line(x: float, y0: float, y1: float,
               amp: float = 3.6, period: float = 15.0) -> str:
    """A vertical line with a short-period wobble, so it reads as a tear.

    One gentle curve over the full height is invisible at this scale; the
    break only registers when the wobble repeats every centimetre or so.
    """
    n = max(2, round((y1 - y0) / period))
    step = (y1 - y0) / n
    d = [f"M {fmt(x)} {fmt(y0)}"]
    for i in range(n):
        ya = y0 + step * i
        cx = x + (amp if i % 2 == 0 else -amp)
        d.append(f"Q {fmt(cx)} {fmt(ya + step / 2)} {fmt(x)} {fmt(ya + step)}")
    return " ".join(d)


def render_row_backgrounds(dg: Diagram) -> str:
    out = []
    w = dg.vcycles * dg.cw
    sig_i = 0
    for row in dg.rows:
        if row.kind not in ("signal", "index"):
            continue
        if sig_i % 2 == 1:
            out.append(f'<rect class="wg-rowalt" x="0" y="{fmt(row.y)}" '
                       f'width="{fmt(w)}" height="{fmt(row.height)}"/>')
        sig_i += 1
    return "".join(out)


def render_marks(dg: Diagram) -> str:
    out = []
    y0, y1 = dg.ruler_y, dg.rows_bottom
    for m in dg.marks:
        if not isinstance(m, dict):
            continue
        colour = m.get("color", "indigo")
        var = f"var(--wg-mark-{colour})" if re.fullmatch(r"[a-z]+", str(colour)) else str(colour)
        label = m.get("label", "")
        if m.get("at") is not None:
            x = dg.x_of(float(m["at"]))
            out.append(f'<line class="wg-markline" x1="{fmt(x)}" y1="{fmt(y0)}" '
                       f'x2="{fmt(x)}" y2="{fmt(y1)}" style="stroke:{var}"/>')
            if label:
                out.append(_chip(x, y0 - 5, label, var, anchor="middle"))
        else:
            a = dg.x_of(float(m.get("from", 0)))
            b = dg.x_of(float(m.get("to", 0)))
            if b < a:
                a, b = b, a
            out.append(
                f'<rect class="wg-markband" x="{fmt(a)}" y="{fmt(y0)}" '
                f'width="{fmt(max(b - a, 1))}" height="{fmt(y1 - y0)}" '
                f'style="fill:{var}"/>')
            out.append(f'<line class="wg-markedge" x1="{fmt(a)}" y1="{fmt(y0)}" '
                       f'x2="{fmt(a)}" y2="{fmt(y1)}" style="stroke:{var}"/>')
            out.append(f'<line class="wg-markedge" x1="{fmt(b)}" y1="{fmt(y0)}" '
                       f'x2="{fmt(b)}" y2="{fmt(y1)}" style="stroke:{var}"/>')
            if label:
                out.append(_chip((a + b) / 2.0, y0 - 5, label, var, anchor="middle"))
    return "".join(out)


def _chip(x: float, y: float, label: str, colour: str, anchor: str = "middle") -> str:
    w = len(label) * 6.0 + 12
    x0 = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
    return (
        f'<g class="wg-chip wg-nox" data-ax="{fmt(x)}">'
        f'<rect x="{fmt(x0)}" y="{fmt(y - 13)}" width="{fmt(w)}" height="15" rx="4" '
        f'style="fill:{colour}"/>'
        f'<text x="{fmt(x0 + w / 2)}" y="{fmt(y - 2.5)}">{esc(label)}</text>'
        f'</g>'
    )


def render_names(dg: Diagram) -> str:
    """Signal names and group brackets, drawn in the left gutter."""
    out = [f'<rect class="wg-gutterbg" x="0" y="0" width="{fmt(dg.name_w)}" '
           f'height="{fmt(dg.height)}"/>']

    for g in dg.groups:
        rows = [r for r in dg.rows if g.first <= r.index <= g.last]
        if not rows:
            continue
        top = rows[0].y + 3
        bot = rows[-1].y + rows[-1].height - 3
        x = PAD_L + g.depth * GROUP_W + 6
        out.append(
            f'<path class="wg-groupbar" d="M {fmt(x + 4)} {fmt(top)} '
            f'Q {fmt(x)} {fmt(top)} {fmt(x)} {fmt(top + 4)} '
            f'L {fmt(x)} {fmt(bot - 4)} Q {fmt(x)} {fmt(bot)} {fmt(x + 4)} {fmt(bot)}"/>')
        if g.label:
            cy = (top + bot) / 2.0
            out.append(
                f'<text class="wg-grouptext" transform="rotate(-90 {fmt(x - 4)} {fmt(cy)})" '
                f'x="{fmt(x - 4)}" y="{fmt(cy)}" dy="0.32em">{esc(g.label)}</text>')

    name_right = dg.name_w - 14
    for row in dg.rows:
        if row.kind != "signal":
            continue
        name = str(row.sig.get("name", ""))
        if not name:
            continue
        _, _, mid = dg.row_rails(row)
        desc = row.sig.get("desc")
        title = f"<title>{esc(desc)}</title>" if desc else ""
        style = f' style="fill:{esc(row.sig["color"])}"' if row.sig.get("color") else ""
        out.append(
            f'<text class="wg-name" x="{fmt(name_right)}" y="{fmt(mid)}" '
            f'dy="0.33em"{style}>{esc(name)}{title}</text>')
    return "".join(out)


_EDGE_RE = re.compile(r"^\s*([A-Za-z0-9])\s*([<>~\-|+*]{1,4})\s*([A-Za-z0-9])\s*(.*)$")


def render_edges(dg: Diagram) -> str:
    """Annotation arrows between named nodes."""
    if not dg.edges:
        return ""
    out = []
    for spec in dg.edges:
        if not isinstance(spec, str):
            continue
        m = _EDGE_RE.match(spec)
        if not m:
            continue
        a, shape, b, label = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        if a not in dg.nodes or b not in dg.nodes:
            continue
        (ax, ay), (bx, by) = dg.nodes[a], dg.nodes[b]

        head_end = shape.endswith(">")
        head_start = shape.startswith("<")
        core = shape.strip("<>")

        if "|" in core:
            if core in ("-|-", "+"):
                mx = (ax + bx) / 2.0
                d = (f"M {fmt(ax)} {fmt(ay)} L {fmt(mx)} {fmt(ay)} "
                     f"L {fmt(mx)} {fmt(by)} L {fmt(bx)} {fmt(by)}")
                lx, ly = mx, (ay + by) / 2.0
            elif core.startswith("|"):
                d = (f"M {fmt(ax)} {fmt(ay)} L {fmt(ax)} {fmt(by)} "
                     f"L {fmt(bx)} {fmt(by)}")
                lx, ly = (ax + bx) / 2.0, by
            else:
                d = (f"M {fmt(ax)} {fmt(ay)} L {fmt(bx)} {fmt(ay)} "
                     f"L {fmt(bx)} {fmt(by)}")
                lx, ly = (ax + bx) / 2.0, ay
        elif "~" in core:
            dx = max(22.0, abs(bx - ax) * 0.42)
            d = (f"M {fmt(ax)} {fmt(ay)} C {fmt(ax + dx)} {fmt(ay)} "
                 f"{fmt(bx - dx)} {fmt(by)} {fmt(bx)} {fmt(by)}")
            lx, ly = (ax + bx) / 2.0, (ay + by) / 2.0
        else:
            d = f"M {fmt(ax)} {fmt(ay)} L {fmt(bx)} {fmt(by)}"
            lx, ly = (ax + bx) / 2.0, (ay + by) / 2.0

        markers = ""
        if head_end:
            markers += ' marker-end="url(#wg-arrow)"'
        if head_start:
            markers += ' marker-start="url(#wg-arrow-rev)"'
        out.append(f'<path class="wg-edgepath" d="{d}"{markers}/>')

        if label:
            w = len(label) * 6.1 + 12
            out.append(
                f'<g class="wg-edgelabel wg-nox" data-ax="{fmt(lx)}">'
                f'<rect x="{fmt(lx - w / 2)}" y="{fmt(ly - 8)}" width="{fmt(w)}" '
                f'height="16" rx="5"/>'
                f'<text x="{fmt(lx)}" y="{fmt(ly)}" dy="0.34em">{esc(label)}</text>'
                f'</g>')

    for name, (nx, ny) in dg.nodes.items():
        out.append(f'<circle class="wg-node wg-nox" data-ax="{fmt(nx)}" '
                   f'cx="{fmt(nx)}" cy="{fmt(ny)}" r="2.6"/>')
        if name.isupper():
            out.append(f'<text class="wg-nodelabel wg-nox" data-ax="{fmt(nx)}" '
                       f'x="{fmt(nx)}" y="{fmt(ny - 8)}">{esc(name)}</text>')
    return "".join(out)


# --------------------------------------------------------------------------
# SVG assembly
# --------------------------------------------------------------------------

SVG_DEFS = """
<defs>
  <pattern id="wg-hatchpat" width="7" height="7" patternUnits="userSpaceOnUse"
           patternTransform="rotate(45)">
    <line class="wg-hatchline" x1="0" y1="0" x2="0" y2="7"/>
  </pattern>
  <marker id="wg-arrow" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path class="wg-arrowhead" d="M 0 0 L 10 5 L 0 10 z"/>
  </marker>
  <marker id="wg-arrow-rev" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path class="wg-arrowhead" d="M 0 0 L 10 5 L 0 10 z"/>
  </marker>
</defs>
"""


SVG_CSS = """
.wg-rail{fill:none;stroke:var(--wg-wave);stroke-width:1.9;stroke-linejoin:round;
  stroke-linecap:round;vector-effect:non-scaling-stroke}
.wg-rail.wg-clk{stroke:var(--wg-wave-clk)}
.wg-bus{stroke-width:1.6;stroke-linejoin:round;vector-effect:non-scaling-stroke}
/* Every stroke inside the time-scaled group keeps its screen width, so
   compressing the time unit thins the diagram's spacing, never its ink. */
.wg-hatchbox,.wg-hatchline,.wg-gapline,.wg-gridline,.wg-tickmark,
.wg-markedge,.wg-markline,.wg-edgepath,.wg-mline,.wg-cursorline,
.wg-edgelabel rect{vector-effect:non-scaling-stroke}
.wg-bustext{font-family:var(--wg-mono);font-size:12px;font-weight:500;
  text-anchor:middle;letter-spacing:.01em;pointer-events:none}
.wg-hatchbox{fill:var(--wg-hatch-bg);stroke:var(--wg-hatch);stroke-width:1.5;
  stroke-linejoin:round}
.wg-hatchfill{fill:url(#wg-hatchpat);stroke:none;opacity:.85}
.wg-hatchline{stroke:var(--wg-hatch);stroke-width:1.1}
.wg-edgemark{fill:var(--wg-wave-clk);opacity:.95}
.wg-gapfill{fill:var(--wg-bg)}
.wg-gapline{fill:none;stroke:var(--wg-faint);stroke-width:1.4;stroke-linecap:round}

.wg-canvasbg{fill:var(--wg-bg)}
.wg-gutterbg{fill:var(--wg-bg)}
.wg-name{font-family:var(--wg-sans);font-size:12.5px;font-weight:500;
  text-anchor:end;fill:var(--wg-text)}
.wg-groupbar{fill:none;stroke:var(--wg-border);stroke-width:1.5}
.wg-grouptext{font-family:var(--wg-sans);font-size:10.5px;font-weight:600;
  letter-spacing:.07em;text-transform:uppercase;text-anchor:middle;
  fill:var(--wg-muted)}

.wg-rulerbg{fill:var(--wg-panel-2);opacity:.55}
.wg-tick{font-family:var(--wg-mono);font-size:10px;fill:var(--wg-faint);
  text-anchor:middle;font-variant-numeric:tabular-nums}
.wg-tickmark{stroke:var(--wg-grid-strong);stroke-width:1}
.wg-tickmark.wg-faintline{stroke:var(--wg-grid);stroke-width:1}
.wg-gridline{stroke:var(--wg-grid);stroke-width:1;shape-rendering:crispEdges}
.wg-gridline-strong{stroke:var(--wg-grid-strong)}
.wg-rowalt{fill:var(--wg-row-alt)}
.wg-rowhit{fill:transparent}
.wg-row.is-hover .wg-rowhit{fill:var(--wg-row-hover)}
.wg-row.is-dim{opacity:.18}
.wg-row{transition:opacity .15s ease}

.wg-foldband{fill:var(--wg-bg)}
.wg-foldedge{fill:none;stroke:var(--wg-fold);stroke-width:1.5;
  stroke-linecap:round;opacity:.8;vector-effect:non-scaling-stroke}
.wg-idxcell{fill:var(--wg-panel-2);opacity:.9}
.wg-idxcell.wg-idxalt{opacity:.45}
.wg-idxtext{font-family:var(--wg-mono);font-size:10.5px;fill:var(--wg-faint);
  text-anchor:middle;font-variant-numeric:tabular-nums;pointer-events:none}

.wg-markband{opacity:var(--wg-markband-op)}
.wg-markedge{stroke-width:1;stroke-dasharray:3 3;opacity:.5}
.wg-markline{stroke-width:1.4;stroke-dasharray:5 3;opacity:.75}
.wg-chip rect{opacity:.92}
.wg-chip text{font-family:var(--wg-sans);font-size:9.5px;font-weight:600;
  letter-spacing:.04em;text-anchor:middle;fill:var(--wg-bg)}

.wg-edgepath{fill:none;stroke:var(--wg-edge);stroke-width:1.5;
  stroke-dasharray:4 2.5;stroke-linecap:round}
.wg-arrowhead{fill:var(--wg-edge)}
.wg-edgelabel rect{fill:var(--wg-edge-chip);stroke:var(--wg-edge);stroke-width:1;
  opacity:.97}
.wg-edgelabel text{font-family:var(--wg-sans);font-size:10px;font-weight:600;
  text-anchor:middle;fill:var(--wg-edge-text)}
.wg-node{fill:var(--wg-edge);opacity:.85}
.wg-nodelabel{font-family:var(--wg-mono);font-size:9.5px;font-weight:600;
  text-anchor:middle;fill:var(--wg-edge-text)}

.wg-cursorline{stroke:var(--wg-cursor);stroke-width:1.2;stroke-dasharray:4 3;
  opacity:0}
.wg-cursor-on .wg-cursorline{opacity:.9}
.wg-cursorchip rect{fill:var(--wg-cursor);opacity:0}
.wg-cursorchip text{font-family:var(--wg-mono);font-size:10px;font-weight:700;
  text-anchor:middle;fill:var(--wg-bg);opacity:0}
.wg-cursor-on .wg-cursorchip rect,.wg-cursor-on .wg-cursorchip text{opacity:1}
.wg-mline{stroke:var(--wg-measure);stroke-width:1.4;opacity:0}
.wg-mband{fill:var(--wg-measure);opacity:0}
.wg-m-on .wg-mline{opacity:.95}
.wg-m-on .wg-mband{opacity:.11}
.wg-mchip rect{fill:var(--wg-measure);opacity:0}
.wg-mchip text{font-family:var(--wg-mono);font-size:10.5px;font-weight:700;
  text-anchor:middle;fill:#fff;opacity:0}
.wg-m-on .wg-mchip rect,.wg-m-on .wg-mchip text{opacity:1}
"""

# Per-slot bus colours, generated so the classes always match theme_vars().
SVG_CSS += "".join(
    f".wg-b{i}{{fill:var(--wg-b{i}-fill);stroke:var(--wg-b{i}-stroke)}}"
    f".wg-b{i}-t{{fill:var(--wg-b{i}-text)}}"
    for i in range(BUS_SLOT_COUNT)
)


def build_svg(dg: Diagram, standalone: bool = False, theme: str | None = None) -> str:
    body = []
    body.append(SVG_DEFS)
    # Paint the full canvas so the SVG is self-sufficient when saved out of the
    # page (an unpainted SVG composites onto whatever ground the viewer supplies).
    body.append(f'<rect class="wg-canvasbg" id="wg-canvasbg" x="0" y="0" '
                f'width="{fmt(dg.width)}" height="{fmt(dg.height)}"/>')

    # The gutter offset stays outside the scaled group; everything measured in
    # time goes inside it, so the page can restretch the x axis on its own.
    waves = [f'<g class="wg-waves" transform="translate({fmt(dg.name_w)},0)">',
             f'<g class="wg-xscale" id="wg-xscale">']
    waves.append(f'<g class="wg-layer-bg">{render_row_backgrounds(dg)}</g>')
    waves.append(f'<g class="wg-layer-grid">{render_grid(dg)}</g>')
    waves.append(f'<g class="wg-layer-marks">{render_marks(dg)}</g>')
    waves.append(f'<g class="wg-layer-ruler" transform="translate(0,{fmt(dg.ruler_y)})">'
                 f'{render_ruler(dg)}</g>')

    w = dg.vcycles * dg.cw
    for row in dg.rows:
        if row.kind not in ("signal", "index"):
            continue
        name = esc(row.sig.get("name", ""))
        content = (render_index_row(dg, row) if row.kind == "index"
                   else render_row_wave(dg, row))
        waves.append(
            f'<g class="wg-row" data-name="{name.lower()}" data-y="{fmt(row.y)}">'
            f'<rect class="wg-rowhit" x="0" y="{fmt(row.y)}" width="{fmt(w)}" '
            f'height="{fmt(row.height)}"/>'
            f'{content}'
            f'</g>')

    # Break bands sit above the traces so a folded span reads as elided time.
    waves.append(f'<g class="wg-layer-folds">{render_folds(dg)}</g>')
    waves.append(f'<g class="wg-layer-edges">{render_edges(dg)}</g>')

    # Interactive overlays (driven by the page script).
    y0, y1 = dg.ruler_y, dg.rows_bottom
    waves.append(
        f'<g class="wg-overlay">'
        f'<rect class="wg-mband" id="wg-mband" x="0" y="{fmt(y0)}" width="0" '
        f'height="{fmt(y1 - y0)}"/>'
        f'<line class="wg-mline" id="wg-mline-a" x1="0" y1="{fmt(y0)}" x2="0" '
        f'y2="{fmt(y1)}"/>'
        f'<line class="wg-mline" id="wg-mline-b" x1="0" y1="{fmt(y0)}" x2="0" '
        f'y2="{fmt(y1)}"/>'
        f'<g class="wg-mchip" id="wg-mchip">'
        f'<rect x="0" y="{fmt(y1 + 2)}" width="0" height="16" rx="5"/>'
        f'<text x="0" y="{fmt(y1 + 13)}"></text></g>'
        f'<line class="wg-cursorline" id="wg-cursorline" x1="0" y1="{fmt(y0)}" '
        f'x2="0" y2="{fmt(y1)}"/>'
        f'<g class="wg-cursorchip" id="wg-cursorchip">'
        f'<rect x="0" y="2" width="44" height="15" rx="4"/>'
        f'<text x="22" y="13"></text></g>'
        f'</g>')
    waves.append("</g></g>")
    body.append("".join(waves))

    body.append(f'<g class="wg-gutter" id="wg-gutter">{render_names(dg)}</g>')

    style = ""
    if standalone:
        tokens = theme_vars(theme or dg.theme)
        decl = ";".join(f"{k}:{v}" for k, v in tokens.items())
        style = (f"<style>:root{{{decl};"
                 f"--wg-sans:ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif;"
                 f"--wg-mono:ui-monospace,'SF Mono','JetBrains Mono',Menlo,Consolas,monospace}}"
                 f"{SVG_CSS}</style>")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" id="wg-svg" '
        f'width="{fmt(dg.width)}" height="{fmt(dg.height)}" '
        f'viewBox="0 0 {fmt(dg.width)} {fmt(dg.height)}" '
        f'data-cw="{fmt(dg.cw)}" data-namew="{fmt(dg.name_w)}" '
        f'data-cycles="{fmt(dg.cycles)}" data-basew="{fmt(dg.width)}" '
        f'data-baseh="{fmt(dg.height)}" data-padr="{fmt(PAD_R)}" '
        f'data-busfont="{fmt(BUS_FONT)}" data-vcycles="{fmt(dg.vcycles)}" '
        f'data-foldw="{fmt(dg.fold_w)}" '
        f'data-folds="{esc(json.dumps([[f.a, f.b] for f in dg.folds]))}">'
        f'{style}{"".join(body)}</svg>'
    )


# ===========================================================================
# HTML page
# ===========================================================================

UI_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --wg-sans:'Inter',ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif;
  --wg-mono:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace;
}
html,body{height:100%}
body{background:var(--wg-bg);color:var(--wg-text);font-family:var(--wg-sans);
  font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column}

.wg-topbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
  padding:12px 20px;background:var(--wg-panel);
  border-bottom:1px solid var(--wg-border);position:sticky;top:0;z-index:20}
.wg-brand{display:flex;flex-direction:column;gap:1px;margin-right:auto;min-width:0}
.wg-title{font-size:15px;font-weight:650;letter-spacing:-.01em;color:var(--wg-text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wg-subtitle{font-size:11.5px;color:var(--wg-muted);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}

.wg-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.wg-seg{display:flex;align-items:center;background:var(--wg-panel-2);
  border:1px solid var(--wg-border);border-radius:8px;overflow:hidden}
.wg-seg button{background:none;border:none;color:var(--wg-muted);cursor:pointer;
  font-family:var(--wg-mono);font-size:12px;padding:6px 10px;line-height:1;
  transition:background .12s,color .12s}
.wg-seg button:hover{background:var(--wg-accent-soft);color:var(--wg-text)}
.wg-seg button:focus-visible{outline:2px solid var(--wg-accent);outline-offset:-2px}
.wg-seg .wg-zoomval{font-family:var(--wg-mono);font-size:11.5px;color:var(--wg-muted);
  padding:0 8px;min-width:52px;text-align:center;
  font-variant-numeric:tabular-nums;border-left:1px solid var(--wg-border);
  border-right:1px solid var(--wg-border)}

.wg-btn{background:var(--wg-panel-2);border:1px solid var(--wg-border);
  border-radius:8px;color:var(--wg-muted);cursor:pointer;font-family:var(--wg-sans);
  font-size:12px;font-weight:550;padding:6px 12px;transition:all .12s}
.wg-btn:hover{background:var(--wg-accent-soft);color:var(--wg-text);
  border-color:var(--wg-accent)}
.wg-btn:focus-visible{outline:2px solid var(--wg-accent);outline-offset:2px}
.wg-btn.is-on{background:var(--wg-accent);border-color:var(--wg-accent);color:#fff}

.wg-scale{display:flex;align-items:center;gap:9px;background:var(--wg-panel-2);
  border:1px solid var(--wg-border);border-radius:8px;padding:5px 11px}
.wg-scale label{font-size:11px;font-weight:550;color:var(--wg-muted);
  white-space:nowrap;letter-spacing:.01em}
.wg-scale output{font-family:var(--wg-mono);font-size:11.5px;color:var(--wg-text);
  font-variant-numeric:tabular-nums;min-width:42px;text-align:right}
#wg-cwrange{-webkit-appearance:none;appearance:none;background:none;
  width:132px;height:16px;cursor:ew-resize;margin:0}
#wg-cwrange:focus-visible{outline:2px solid var(--wg-accent);outline-offset:3px;
  border-radius:4px}
#wg-cwrange::-webkit-slider-runnable-track{height:4px;border-radius:3px;
  background:var(--wg-border)}
#wg-cwrange::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;
  width:13px;height:13px;border-radius:50%;background:var(--wg-accent);
  border:2px solid var(--wg-panel);margin-top:-4.5px;
  transition:transform .12s}
#wg-cwrange:hover::-webkit-slider-thumb{transform:scale(1.15)}
#wg-cwrange::-moz-range-track{height:4px;border-radius:3px;
  background:var(--wg-border)}
#wg-cwrange::-moz-range-thumb{width:11px;height:11px;border-radius:50%;
  background:var(--wg-accent);border:2px solid var(--wg-panel)}

.wg-search{background:var(--wg-panel-2);border:1px solid var(--wg-border);
  border-radius:8px;color:var(--wg-text);font-family:var(--wg-sans);font-size:12px;
  padding:6px 10px;width:150px;transition:border-color .12s}
.wg-search::placeholder{color:var(--wg-faint)}
.wg-search:focus{outline:none;border-color:var(--wg-accent)}

.wg-stage{flex:1;min-height:0;position:relative;overflow:auto;padding:18px 0 24px}
.wg-stage::-webkit-scrollbar{height:11px;width:11px}
.wg-stage::-webkit-scrollbar-thumb{background:var(--wg-border);border-radius:6px}
.wg-stage::-webkit-scrollbar-thumb:hover{background:var(--wg-faint)}
#wg-svg{display:block;user-select:none;cursor:crosshair}
.wg-gutter{filter:drop-shadow(2px 0 6px var(--wg-shadow))}

.wg-footbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:9px 20px;background:var(--wg-panel);border-top:1px solid var(--wg-border);
  font-size:11.5px;color:var(--wg-muted)}
.wg-foottext{margin-right:auto}
.wg-hint{font-family:var(--wg-mono);font-size:10.5px;color:var(--wg-faint)}
.wg-kbd{background:var(--wg-panel-2);border:1px solid var(--wg-border);
  border-bottom-width:2px;border-radius:4px;font-family:var(--wg-mono);
  font-size:10px;padding:1px 5px;color:var(--wg-muted)}
.wg-readout{font-family:var(--wg-mono);font-size:11.5px;color:var(--wg-text);
  font-variant-numeric:tabular-nums;min-width:190px}
.wg-readout b{color:var(--wg-accent);font-weight:700}
"""


PAGE_JS = """
(function(){
  'use strict';
  var SVGNS='http://www.w3.org/2000/svg';
  var root=document.documentElement;
  var svg=document.getElementById('wg-svg');
  var stage=document.getElementById('wg-stage');
  var gutter=document.getElementById('wg-gutter');
  var BASECW=parseFloat(svg.dataset.cw), NAMEW=parseFloat(svg.dataset.namew);
  var CYCLES=parseFloat(svg.dataset.cycles);
  var BASEW=parseFloat(svg.dataset.basew), BASEH=parseFloat(svg.dataset.baseh);
  var PADR=parseFloat(svg.dataset.padr), BUSFONT=parseFloat(svg.dataset.busfont);
  var CW=BASECW;   /* live width of one time unit, in px */

  /* ---- theme ------------------------------------------------------- */
  var themeBtn=document.getElementById('wg-theme');
  var THEMES=['light','dark','print'];   /* button shows the next one */
  function setTheme(t){
    if(THEMES.indexOf(t)<0) t='dark';
    root.setAttribute('data-theme',t);
    var nxt=THEMES[(THEMES.indexOf(t)+1)%THEMES.length];
    themeBtn.textContent=nxt.charAt(0).toUpperCase()+nxt.slice(1);
    try{localStorage.setItem('wavegen-theme',t);}catch(e){}
  }
  try{
    var saved=localStorage.getItem('wavegen-theme');
    if(saved){setTheme(saved);} else {setTheme(root.getAttribute('data-theme')||'dark');}
  }catch(e){setTheme(root.getAttribute('data-theme')||'dark');}
  themeBtn.addEventListener('click',function(){
    var cur=root.getAttribute('data-theme');
    setTheme(THEMES[(THEMES.indexOf(cur)+1)%THEMES.length]);
  });

  /* ---- horizontal time-unit scale ---------------------------------- */
  /* The whole wave layer lives inside #wg-xscale.  Stretching only its x axis
     rescales time without touching row heights, so anything that must stay
     legible (text, chips, node dots, edge marks) carries .wg-nox and is
     counter-scaled about its own anchor.                                    */
  var xscale=document.getElementById('wg-xscale');
  var canvasbg=document.getElementById('wg-canvasbg');
  var rulerbg=document.getElementById('wg-rulerbg');
  var hatch=document.getElementById('wg-hatchpat');
  var range=document.getElementById('wg-cwrange');
  /* The time axis is piecewise when folds are present: a folded span collapses
     to FOLDW cycle-units.  u() maps a real cycle to visible units and cyc() is
     its inverse, so hover and measurement always report true cycle numbers. */
  var FOLDS=JSON.parse(svg.dataset.folds||'[]');
  var FOLDW=parseFloat(svg.dataset.foldw)||0;
  var VCYCLES=parseFloat(svg.dataset.vcycles);
  function u(cycle){
    var acc=0, prev=0;
    for(var i=0;i<FOLDS.length;i++){
      var a=FOLDS[i][0], b=FOLDS[i][1];
      if(cycle<=a) break;
      acc+=a-prev;
      if(cycle<b) return acc+FOLDW*(cycle-a)/(b-a);
      acc+=FOLDW; prev=b;
    }
    return acc+cycle-prev;
  }
  function cyc(units){
    var acc=0, prev=0;
    for(var i=0;i<FOLDS.length;i++){
      var a=FOLDS[i][0], b=FOLDS[i][1];
      var head=acc+(a-prev);
      if(units<=head) break;
      if(units<head+FOLDW) return a+(b-a)*(units-head)/FOLDW;
      acc=head+FOLDW; prev=b;
    }
    return prev+(units-acc);
  }
  function foldAt(cycle){
    for(var i=0;i<FOLDS.length;i++){
      if(cycle>FOLDS[i][0] && cycle<FOLDS[i][1]) return FOLDS[i];
    }
    return null;
  }
  function inFold(cycle){ return foldAt(cycle)!==null; }
  /* Overlays live inside the scaled group, so they are positioned in unscaled
     units: one visible cycle unit is BASECW wide there. */
  function ux(cycle){ return u(cycle)*BASECW; }
  var cwOut=document.getElementById('wg-cwval');
  var noxEls=[].slice.call(svg.querySelectorAll('.wg-nox'));
  var busText=noxEls.filter(function(el){return el.dataset.avail!==undefined;});
  var ticks=[].slice.call(svg.querySelectorAll('.wg-tick'));
  var idxText=[].slice.call(svg.querySelectorAll('.wg-idxtext'));
  var MINCW=parseFloat(range.min), MAXCW=parseFloat(range.max);

  function counterScale(el,k){
    var ax=parseFloat(el.dataset.ax)||0;
    el.setAttribute('transform',
      'translate('+(ax-ax/k).toFixed(3)+',0) scale('+(1/k).toFixed(5)+',1)');
  }
  function fitText(s,avail){
    if(!s) return '';
    var n=Math.floor(avail/(BUSFONT*0.60));
    if(s.length<=n) return s;
    if(n<2) return '';          /* matches _fit_text() in wavegen.py */
    return s.slice(0,n-1)+'\\u2026';
  }
  function applyCw(v,fromSlider){
    CW=Math.min(MAXCW,Math.max(MINCW,v));
    var k=CW/BASECW;
    xscale.setAttribute('transform','scale('+k.toFixed(6)+',1)');
    BASEW=NAMEW+VCYCLES*CW+PADR;
    svg.setAttribute('width',BASEW.toFixed(2));
    svg.setAttribute('viewBox','0 0 '+BASEW.toFixed(2)+' '+BASEH);
    canvasbg.setAttribute('width',BASEW.toFixed(2));
    noxEls.forEach(function(el){counterScale(el,k);});
    /* Bus labels get the room the brick actually has on screen. */
    busText.forEach(function(el){
      el.textContent=fitText(el.dataset.full,parseFloat(el.dataset.avail)*k);
      var t=document.createElementNS(SVGNS,'title');
      t.textContent=el.dataset.full; el.appendChild(t);
    });
    /* Thin cycle numbers so they never collide. */
    var every=Math.max(1,Math.ceil(26/CW));
    if(every>2) every=Math.ceil(every/5)*5;
    ticks.forEach(function(t){
      t.style.display=(parseInt(t.dataset.cyc,10)%every===0)?'':'none';
    });
    /* Step-index cells hide their number once it no longer fits the cell. */
    idxText.forEach(function(t){
      var room=parseFloat(t.dataset.idxw)*k;
      t.style.display=(room>=t.textContent.length*7+4)?'':'none';
    });
    if(hatch) hatch.setAttribute('patternTransform',
      'scale('+(1/k).toFixed(5)+',1) rotate(45)');
    cwOut.textContent=Math.round(CW)+'px';
    if(!fromSlider) range.value=CW;
    applyZoom();
  }

  /* ---- zoom -------------------------------------------------------- */
  var zoom=1, zoomVal=document.getElementById('wg-zoomval');
  function applyZoom(){
    zoom=Math.min(4,Math.max(0.35,zoom));
    svg.style.width=(BASEW*zoom)+'px';
    svg.style.height=(BASEH*zoom)+'px';
    zoomVal.textContent=Math.round(zoom*100)+'%';
  }
  function fit(){
    /* Fit the diagram to the window by narrowing the time unit first, and only
       then shrinking overall if the names alone still overflow. */
    var avail=stage.clientWidth-24;
    zoom=1;
    applyCw((avail-NAMEW-PADR)/VCYCLES);
    if(BASEW>avail){ zoom=avail/BASEW; applyZoom(); }
  }
  range.addEventListener('input',function(){applyCw(parseFloat(range.value),true);});
  document.getElementById('wg-zoomin').addEventListener('click',function(){zoom*=1.25;applyZoom();});
  document.getElementById('wg-zoomout').addEventListener('click',function(){zoom/=1.25;applyZoom();});
  document.getElementById('wg-zoomfit').addEventListener('click',fit);
  document.getElementById('wg-zoomreset').addEventListener('click',function(){
    zoom=1; applyCw(BASECW);
  });
  range.value=BASECW;
  applyCw(BASECW);

  /* ---- sticky name gutter ------------------------------------------ */
  stage.addEventListener('scroll',function(){
    gutter.setAttribute('transform','translate('+(stage.scrollLeft/zoom)+',0)');
  },{passive:true});

  /* ---- coordinate helper ------------------------------------------- */
  function svgX(evt){
    var ctm=svg.getScreenCTM();
    if(!ctm) return null;
    var pt=svg.createSVGPoint(); pt.x=evt.clientX; pt.y=evt.clientY;
    return pt.matrixTransform(ctm.inverse()).x - NAMEW;
  }
  function toCycle(x){ return cyc(x/CW); }
  function clampCycle(c){ return Math.min(CYCLES,Math.max(0,c)); }
  /* Snap to half-cycles, but never onto a cycle hidden inside a fold. */
  function snap(c){ var s=Math.round(c*2)/2; return inFold(s)?c:s; }

  /* ---- hover cursor ------------------------------------------------ */
  var line=document.getElementById('wg-cursorline');
  var chip=document.getElementById('wg-cursorchip');
  var chipRect=chip.querySelector('rect'), chipText=chip.querySelector('text');
  var readout=document.getElementById('wg-readout');
  var rows=[].slice.call(svg.querySelectorAll('.wg-row'));

  function clearHover(){
    svg.classList.remove('wg-cursor-on');
    rows.forEach(function(r){r.classList.remove('is-hover');});
  }

  svg.addEventListener('mousemove',function(e){
    var x=svgX(e);
    if(x===null||x<-4){clearHover();return;}
    var c=clampCycle(toCycle(x));
    var snapped=snap(c);
    var px=ux(snapped);
    svg.classList.add('wg-cursor-on');
    line.setAttribute('x1',px); line.setAttribute('x2',px);
    chipRect.setAttribute('x',px-22); chipText.setAttribute('x',px);
    chip.dataset.ax=px; counterScale(chip,CW/BASECW);
    chipText.textContent=(Math.round(snapped*100)/100).toFixed(1);

    var ctm=svg.getScreenCTM();
    var pt=svg.createSVGPoint(); pt.x=e.clientX; pt.y=e.clientY;
    var y=pt.matrixTransform(ctm.inverse()).y;
    var hit=null;
    rows.forEach(function(r){
      r.classList.remove('is-hover');
      var ry=parseFloat(r.dataset.y);
      var rect=r.querySelector('.wg-rowhit');
      var rh=parseFloat(rect.getAttribute('height'));
      if(y>=ry&&y<ry+rh) hit=r;
    });
    if(hit) hit.classList.add('is-hover');
    updateReadout(snapped, hit);
  });
  svg.addEventListener('mouseleave',clearHover);

  /* ---- click to measure -------------------------------------------- */
  var mA=null,mB=null;
  var lineA=document.getElementById('wg-mline-a'), lineB=document.getElementById('wg-mline-b');
  var band=document.getElementById('wg-mband');
  var mchip=document.getElementById('wg-mchip');
  var mchipR=mchip.querySelector('rect'), mchipT=mchip.querySelector('text');

  function drawMeasure(){
    if(mA===null||mB===null){svg.classList.remove('wg-m-on');return;}
    var a=ux(Math.min(mA,mB)), b=ux(Math.max(mA,mB));
    svg.classList.add('wg-m-on');
    lineA.setAttribute('x1',a); lineA.setAttribute('x2',a);
    lineB.setAttribute('x1',b); lineB.setAttribute('x2',b);
    band.setAttribute('x',a); band.setAttribute('width',Math.max(b-a,0));
    var d=Math.abs(mB-mA), label='\\u0394 '+(Math.round(d*100)/100)+' cyc';
    var w=label.length*7+16, cx=(a+b)/2;
    mchipR.setAttribute('x',cx-w/2); mchipR.setAttribute('width',w);
    mchipT.setAttribute('x',cx); mchipT.textContent=label;
    mchip.dataset.ax=cx; counterScale(mchip,CW/BASECW);
    updateReadout(null,null);
  }
  svg.addEventListener('click',function(e){
    var x=svgX(e); if(x===null||x<-4) return;
    var c=snap(clampCycle(toCycle(x)));
    if(mA===null||mB!==null){mA=c;mB=null;svg.classList.remove('wg-m-on');}
    else{mB=c;drawMeasure();}
    updateReadout(c,null);
  });
  svg.addEventListener('contextmenu',function(e){
    e.preventDefault(); mA=mB=null; svg.classList.remove('wg-m-on'); updateReadout(null,null);
  });

  function updateReadout(cycle,row){
    var bits=[];
    var f=(cycle===null||cycle===undefined)?null:foldAt(cycle);
    if(f){
      /* Inside a band, 32 cycles share ~100px; a fractional cycle would be
         noise, so name the folded span instead. */
      bits.push('folded <b>'+f[0]+'\\u2013'+f[1]+'</b> ('+(f[1]-f[0])+' cyc)');
    }
    else if(cycle!==null&&cycle!==undefined) bits.push('cycle <b>'+cycle.toFixed(1)+'</b>');
    if(row&&row.dataset.name) bits.push(row.dataset.name);
    if(mA!==null&&mB!==null) bits.push('\\u0394 <b>'+(Math.round(Math.abs(mB-mA)*100)/100)+'</b> cyc');
    else if(mA!==null) bits.push('A@'+mA.toFixed(1)+' \\u2026 click B');
    readout.innerHTML=bits.join(' \\u00b7 ')||'hover the diagram';
  }
  updateReadout(null,null);

  /* ---- search / dim ------------------------------------------------ */
  var search=document.getElementById('wg-search');
  search.addEventListener('input',function(){
    var q=search.value.trim().toLowerCase();
    rows.forEach(function(r){
      r.classList.toggle('is-dim', !!q && r.dataset.name.indexOf(q)===-1);
    });
  });

  /* ---- export ------------------------------------------------------ */
  var VARS=__VARS__;
  var SVGCSS=__SVGCSS__;
  document.getElementById('wg-export').addEventListener('click',function(){
    var clone=svg.cloneNode(true);
    clone.removeAttribute('style');
    clone.setAttribute('width',BASEW); clone.setAttribute('height',BASEH);
    var g=clone.querySelector('#wg-gutter');
    if(g) g.setAttribute('transform','translate(0,0)');
    ['wg-cursor-on','wg-m-on'].forEach(function(c){clone.classList.remove(c);});
    var cs=getComputedStyle(root);
    var decl=VARS.map(function(v){return v+':'+cs.getPropertyValue(v).trim();}).join(';');
    decl+=";--wg-sans:ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif";
    decl+=";--wg-mono:ui-monospace,'SF Mono','JetBrains Mono',Menlo,Consolas,monospace";
    var st=document.createElementNS(SVGNS,'style');
    st.textContent=':root{'+decl+'}'+SVGCSS;
    clone.insertBefore(st,clone.firstChild);
    var out='<?xml version="1.0" encoding="UTF-8"?>\\n'+new XMLSerializer().serializeToString(clone);
    var url=URL.createObjectURL(new Blob([out],{type:'image/svg+xml'}));
    var a=document.createElement('a');
    var base=(document.title||'waveform').replace(/[^A-Za-z0-9._-]+/g,'-')
             .replace(/^-+|-+$/g,'').slice(0,80)||'waveform';
    a.href=url; a.download=base+'.svg';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(url);},1500);
  });

  /* ---- keyboard ---------------------------------------------------- */
  document.addEventListener('keydown',function(e){
    if(e.target.tagName==='INPUT') { if(e.key==='Escape') e.target.blur(); return; }
    if(e.key==='+'||e.key==='='){zoom*=1.25;applyZoom();}
    else if(e.key==='-'||e.key==='_'){zoom/=1.25;applyZoom();}
    else if(e.key==='0'){zoom=1;applyCw(BASECW);}
    else if(e.key==='['){applyCw(CW-Math.max(1,CW*0.15));}
    else if(e.key===']'){applyCw(CW+Math.max(1,CW*0.15));}
    else if(e.key==='f'){fit();}
    else if(e.key==='t'){setTheme(root.getAttribute('data-theme')==='dark'?'light':'dark');}
    else if(e.key==='/'){e.preventDefault();search.focus();}
    else if(e.key==='Escape'){mA=mB=null;svg.classList.remove('wg-m-on');updateReadout(null,null);}
  });
})();
"""


def build_html(dg: Diagram) -> str:
    light = ";".join(f"{k}:{v}" for k, v in theme_vars("light").items())
    dark = ";".join(f"{k}:{v}" for k, v in theme_vars("dark").items())
    printed = ";".join(f"{k}:{v}" for k, v in theme_vars("print").items())

    js = (PAGE_JS
          .replace("__VARS__", json.dumps(VAR_NAMES))
          .replace("__SVGCSS__", json.dumps(SVG_CSS)))

    foot_text = dg.foot.get("text", "")
    subtitle = dg.subtitle or (
        f"{sum(1 for r in dg.rows if r.kind == 'signal')} signals · "
        f"{int(dg.cycles)} cycles")

    return f"""<!doctype html>
<html lang="en" data-theme="{esc(dg.theme)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(dg.title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
:root{{{light}}}
[data-theme="dark"]{{{dark}}}
[data-theme="print"]{{{printed}}}
{UI_CSS}
{SVG_CSS}
</style>
</head>
<body>

<div class="wg-topbar">
  <div class="wg-brand">
    <div class="wg-title">{esc(dg.title)}</div>
    <div class="wg-subtitle">{esc(subtitle)}</div>
  </div>
  <div class="wg-tools">
    <input id="wg-search" class="wg-search" type="search" placeholder="Filter signals…"
           aria-label="Filter signals">
    <div class="wg-scale">
      <label for="wg-cwrange">Time unit</label>
      <input id="wg-cwrange" type="range" min="6" max="160" step="1"
             aria-label="Width of one time unit in pixels">
      <output id="wg-cwval" for="wg-cwrange">46px</output>
    </div>
    <div class="wg-seg" role="group" aria-label="Zoom">
      <button id="wg-zoomout" title="Zoom out (-)" aria-label="Zoom out">−</button>
      <span class="wg-zoomval" id="wg-zoomval">100%</span>
      <button id="wg-zoomin" title="Zoom in (+)" aria-label="Zoom in">+</button>
      <button id="wg-zoomfit" title="Fit the whole diagram to the window (f)">Fit</button>
      <button id="wg-zoomreset" title="Reset zoom and time unit (0)">1:1</button>
    </div>
    <button id="wg-export" class="wg-btn" title="Download the diagram as SVG">Export SVG</button>
    <button id="wg-theme" class="wg-btn" title="Toggle theme (t)">Light</button>
  </div>
</div>

<div class="wg-stage" id="wg-stage">
{build_svg(dg)}
</div>

<div class="wg-footbar">
  <span class="wg-foottext">{esc(foot_text)}</span>
  <span class="wg-readout" id="wg-readout"></span>
  <span class="wg-hint">
    <span class="wg-kbd">click</span> set A/B ·
    <span class="wg-kbd">esc</span> clear ·
    <span class="wg-kbd">[</span><span class="wg-kbd">]</span> time unit ·
    <span class="wg-kbd">/</span> search ·
    <span class="wg-kbd">t</span> theme ·
    <span class="wg-kbd">f</span> fit
  </span>
</div>

<script>{js}</script>
</body>
</html>
"""


# ===========================================================================
# CLI
# ===========================================================================

def load_doc(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"wavegen: input file not found: {path}")
    # Tolerate // and /* */ comments and trailing commas, which are common in
    # hand-written WaveJSON.
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"(?m)^\s*//.*$", "", raw)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"wavegen: {path}: invalid JSON at line {exc.lineno}, "
                         f"column {exc.colno}: {exc.msg}")
    if not isinstance(doc, dict):
        raise SystemExit(f"wavegen: {path}: top level must be a JSON object")
    if "signal" not in doc:
        raise SystemExit(f"wavegen: {path}: missing required \"signal\" array")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wavegen.py",
        description="Render interface timing waveforms from a WaveJSON description.")
    ap.add_argument("-i", "--input", default="in.json", type=Path,
                    help="input WaveJSON file (default: in.json)")
    ap.add_argument("-o", "--output", default="out.html", type=Path,
                    help="output HTML file (default: out.html)")
    ap.add_argument("--svg", type=Path, default=None,
                    help="also write a standalone SVG to this path")
    ap.add_argument("--theme", choices=("dark", "light", "print"), default=None,
                    help="override config.theme")
    ap.add_argument("--hscale", type=float, default=None, help="override config.hscale")
    ap.add_argument("--vscale", type=float, default=None, help="override config.vscale")
    ap.add_argument("--no-grid", action="store_true", help="disable the cycle grid")
    ap.add_argument("--version", action="version", version=f"wavegen {__version__}")
    args = ap.parse_args(argv)

    doc = load_doc(args.input)
    overrides: dict[str, Any] = {
        "theme": args.theme,
        "hscale": args.hscale,
        "vscale": args.vscale,
    }
    if args.no_grid:
        overrides["grid"] = False

    dg = Diagram(doc, overrides)

    args.output.write_text(build_html(dg), encoding="utf-8")
    if args.svg:
        args.svg.write_text(build_svg(dg, standalone=True), encoding="utf-8")

    n_sig = sum(1 for r in dg.rows if r.kind == "signal")
    print(f"wavegen {__version__}: {args.input} -> {args.output}")
    print(f"  {n_sig} signals, {len(dg.groups)} groups, "
          f"{int(dg.cycles)} cycles, {int(dg.width)}x{int(dg.height)} px, "
          f"theme={dg.theme}")
    if args.svg:
        print(f"  standalone SVG -> {args.svg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
