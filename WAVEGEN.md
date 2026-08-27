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
24 cycles, 26 signals, five channel groups, with slave back-pressure on the W
channel and master throttling on the R channel.

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
| `u` `d` | weak pull-up / pull-down (eased transition)                   |
| `z`     | high impedance (mid rail)                                     |
| `x` `X` | don't care (hatched)                                          |
| `=`     | data / bus brick, neutral colour                              |
| `2`–`9` | data / bus brick, eight further colour slots                  |
| `.`     | repeat — extends a level or bus, or emits another clock pulse |
| `\|`    | gap — draws a break symbol over the held value                |
| ` `     | blank, nothing drawn                                          |

Each `=` or `2`–`9` brick consumes one entry from that signal's `data` array;
a following `.` extends the brick without consuming another value.

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

`config` accepts `theme` (`dark`\|`light`), `hscale`, `vscale`, `grid`,
`clockArrows` (`explicit`\|`all`), `title`, and `subtitle`.

`marks` entries are either a band — `{"from": 2, "to": 4, "label": "AW",
"color": "indigo"}` — or a cursor — `{"at": 8, "label": "WLAST"}`. Named colours
are `indigo`, `teal`, `amber`, `rose`, `cyan`, `lime`, `orange`, `violet`,
`slate`; any CSS colour also works.

Edge shapes: `-` straight, `~` spline, `-|` / `|-` / `-|-` orthogonal, with
`<` and `>` adding arrowheads (`a<->b`, `c~>d`).

## Extensions over stock WaveDrom

* **Horizontal time-unit slider** — set the pixel width of one step, from 6 px to
  160 px, to bring a long waveform into view. Only the time axis rescales: row
  heights, signal names, bus labels, tick numbers and annotation chips keep their
  size, bus labels re-truncate to the room actually available (the full value stays
  in the tooltip), and cycle numbers thin out to avoid collisions. `Fit` sets the
  time unit so the whole diagram fits the window; `1:1` restores it.
  `config.hscale` still sets the starting value.
* Dark and light themes, toggleable in the page and remembered per browser.
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
    --theme    dark | light             (overrides config.theme)
    --hscale   horizontal scale         (overrides config.hscale)
    --vscale   vertical scale           (overrides config.vscale)
    --no-grid  disable the cycle grid
```
