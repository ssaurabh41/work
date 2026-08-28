# wavegen.py

Interface timing waveform generator for IP / protocol documentation.

Reads a [WaveDrom](https://wavedrom.com)-compatible JSON description and renders a
self-contained, interactive HTML page with crisp SVG waveforms. No external
dependencies — standard library only.

```bash
python3 wavegen.py                          # in.json -> out.html
python3 wavegen.py -i axi.json -o axi.html
python3 wavegen.py --theme light --hscale 1.5
python3 wavegen.py --svg diagram.svg        # also emit a standalone SVG
```

The shipped `in.json` is a full AXI4 write burst followed by a read burst —
54 cycles, 26 signals, five channel groups, with slave back-pressure on the W
channel and master throttling on the R channel. Its 32-cycle memory latency is
folded away, so it renders in the width of 24 cycles.

## Model

A waveform is a sequence of discrete steps ("bricks"). At each step a signal may
hold its level, take a rising edge, or take a falling edge. A clock signal carries
both a rising and a falling edge inside a single step.

## Wave language

| Char    | Meaning                                                       |
| ------- | ------------------------------------------------------------- |
| `0` `1` | low / high level                                              |
| `l` `h` | low / high level (clock-style alias)                          |
| `L` `H` | low / high level, transition marked with an arrow             |
| `p` `n` | clock pulse, positive / negative polarity — two edges per step |
| `P` `N` | clock pulse with the active edge marked                       |
| `f` `F` | fast clock — `cycles_per_unit` whole periods per step *(extension)* |
| `u` `d` | weak pull-up / pull-down (eased transition)                   |
| `z`     | high impedance (mid rail)                                     |
| `x` `X` | don't care (hatched)                                          |
| `=`     | data / bus brick, neutral colour                              |
| `2`–`9` | data / bus brick, eight further colour slots                  |
| `.`     | repeat — extends a level or bus, or emits another clock pulse |
| `c{n}`  | run length — the brick `c` spans **n steps** *(extension)*     |
| `\|`    | gap — draws a break symbol over the held value                |
| ` `     | blank, nothing drawn                                          |

Each `=` or `2`–`9` brick consumes one entry from that signal's `data` array;
a following `.` extends the brick without consuming another value.

`c{n}` saves counting dots by hand: `"1{32}"` is 32 cycles high, `"P{54}"` is 54
clock pulses, `"={4}"` is one bus brick four steps wide. `{` is not a wave
character, so this cannot change an existing diagram. It works in `node`
strings too, so `".{52}f"` puts node `f` at step 52.

**Toggling on the clock's falling edge:** a `p` clock is high for the first half
of a step and low for the second, so its falling edges sit at 0.5, 1.5, 2.5 …
Give a signal `"phase": -0.5` and its transitions land exactly there. (`+0.5`
also lands on falling edges but shifts the wave *left*, losing a half-step off
the front.)

## Signal keys

| Key      | Meaning                                                    |
| -------- | ---------------------------------------------------------- |
| `name`   | label shown in the left gutter                             |
| `wave`   | the wave string                                            |
| `data`   | bus values, as an array or a space-separated string        |
| `node`   | node letters for edge annotations, positioned per step     |
| `period` | stretch factor for this signal                             |
| `phase`  | shift, in cycles, for this signal                          |
| `color`  | stroke / label colour override *(extension)*               |
| `desc`   | tooltip shown on the signal name *(extension)*             |
| `index`  | virtual step-number row, see below *(extension)*           |
| `cycles_per_unit` | fast-clock rate for this signal *(extension)*      |

Nest signals in an array whose first element is a string to form a labelled,
collapsible group; groups may nest arbitrarily. An empty object `{}` is a spacer.

## Document keys

| Key      | Meaning                                                              |
| -------- | -------------------------------------------------------------------- |
| `signal` | the signal tree (required)                                           |
| `config` | see below                                                            |
| `head`   | `{text, tick, tock, every}` — title and cycle-ruler numbering        |
| `foot`   | `{text}` — caption below the diagram                                 |
| `edge`   | annotation arrows, e.g. `"a~>b setup"`                               |
| `marks`  | highlight bands / cursors *(extension)*                              |
| `folds`  | spans of dead time collapsed to a break band *(extension)*            |

`config` accepts `theme` (`dark`\|`light`\|`print`), `hscale`, `vscale`, `grid`,
`clockArrows` (`explicit`\|`all`), `cycles_per_unit`, `foldWidth`, `title`, and
`subtitle`.

`marks` entries are either a band — `{"from": 2, "to": 4, "label": "AW",
"color": "indigo"}` — or a cursor — `{"at": 8, "label": "WLAST"}`. Named colours
are `indigo`, `teal`, `amber`, `rose`, `cyan`, `lime`, `orange`, `violet`,
`slate`; any CSS colour also works.

Edge shapes: `-` straight, `~` spline, `-|` / `|-` / `-|-` orthogonal, with
`<` and `>` adding arrowheads (`a<->b`, `c~>d`).

## Fast clocks

`p` gives one rising and one falling edge per step. `f` (and `F`, which marks the
step's leading edge) packs `cycles_per_unit` whole periods into a single step at
50% duty:

```json
"config": { "cycles_per_unit": 8 },
"signal": [
  { "name": "REFCLK", "wave": "p{10}" },
  { "name": "PLLCLK", "wave": "f{10}" }
]
```

Range 1–32, clamped; the default 1 makes `f` identical to `p`. A signal may
carry its own `"cycles_per_unit"` to override the document rate, so a reference
clock and several derived clocks can share one diagram. `p` and `n` ignore the
setting entirely.

Past roughly ×16 the trace reads as a dense band rather than countable pulses —
that is the honest picture at that ratio, and it is also where the SVG grows
(a 54-step row is ~23 KB at ×1 and ~106 KB at ×32).

## Print theme

`"theme": "print"` renders black-and-white for embedding in a document:

* One ink colour — black traces, text, ticks and annotations on white.
* The nine bus colour slots become a monotonic grey ramp, so buses stay
  tellable apart without hue.
* **No tint between annotation markers.** The dashed boundaries and the section
  label stay; only the coloured band between them is dropped.

Use it from the CLI for a figure — `python3 wavegen.py --theme print --svg fig.svg`
— or from the page's theme button, which cycles Light → Dark → Print.

## Folding dead time

A long pipeline delay costs a lot of width and says little. Declare the idle
span once, in real cycle numbers, and it collapses to a narrow break band:

```json
"folds": [ { "from": 15, "to": 47, "label": "32 cycles — memory latency" } ]
```

`{"from": 15, "cycles": 32}` is equivalent. The shipped `in.json` is 54 cycles
wide but renders in the space of 22.3 — the ruler jumps 14 → 47 across a torn
band, and `config.foldWidth` (default 0.34 cycle-widths) sets how much room the
band keeps.

Time stays honest: hovering reports true cycle numbers on both sides, a
measurement spanning the fold counts the cycles that were elided (35, not the
1.3 you can see), and hovering inside the band names the folded span rather
than inventing a fractional cycle.

A fold that would hide a real transition is refused, naming the signal and
cycle — clock-only rows are exempt, since eliding them is the point. Add
`"force": true` to that fold to render it anyway.

## Step-index row

```json
{ "name": "step", "index": true }
```

A virtual row numbering every step 0, 1, 2 … Optional and purely for
readability; its numbers jump across a fold, which makes the elided span
obvious next to the signals rather than only at the top ruler. Accepts
`{"from": 100, "every": 5}` to offset or thin the numbering.

## Extensions over stock WaveDrom

* **Horizontal time-unit slider** — set the pixel width of one step, from 6 px to
  160 px, to bring a long waveform into view. Only the time axis rescales: row
  heights, signal names, bus labels, tick numbers and annotation chips keep their
  size, bus labels re-truncate to the room actually available (the full value stays
  in the tooltip), and cycle numbers thin out to avoid collisions. `Fit` sets the
  time unit so the whole diagram fits the window; `1:1` restores it.
  `config.hscale` still sets the starting value.
* **Folded dead time** — collapse long idle spans (a pipeline delay, a memory
  latency) to a narrow break band, with the ruler jumping across it and
  measurements still counting the real elapsed cycles.
* **Step-index row** and **`c{n}` run-length** syntax, so long diagrams stay
  readable to write as well as to read.
* **Print theme** — black and white only, grey-ramp buses, no marker band tint,
  for figures going into a document.
* **Fast clocks** — `f`/`F` with `cycles_per_unit` (1–32) puts many clock
  periods inside one time unit at 50% duty.
* Dark, light and print themes, toggleable in the page and remembered per browser.
* Hover time cursor with a live cycle and signal readout.
* Click twice to measure an interval; the delta is shown in cycles.
* Signal search that dims non-matching rows.
* Zoom in / out / fit-to-width / 1:1.
* `Export SVG` writes a standalone, self-styled SVG with the current theme baked in.
* Annotation bands and cursors (`marks`) in a dedicated lane above the ruler.
* Per-signal colour overrides and name tooltips.
* Nine bus colour slots rather than WaveDrom's shared palette.
* Input tolerates `//` and `/* */` comments and trailing commas.

## Keyboard

`+` / `-` zoom · `[` / `]` narrow / widen the time unit · `0` reset both ·
`f` fit · `t` theme · `/` search · `esc` clear measurement · right-click clears
markers.

## CLI

```
-i, --input    input WaveJSON file      (default: in.json)
-o, --output   output HTML file         (default: out.html)
    --svg      also write a standalone SVG to this path
    --theme    dark | light | print     (overrides config.theme)
    --hscale   horizontal scale         (overrides config.hscale)
    --vscale   vertical scale           (overrides config.vscale)
    --no-grid  disable the cycle grid
```
