# Case generation: `create_case` / `edit_case` / `describe_case`

Design notes for the "design" stage of the workflow: the agent describes a 2D
dam-break-family experiment in structured parameters, and the server renders a
GenCase `*_Def.xml`. Pure Python — no solver involved until `gencase`. The
toolchain behaviours that shaped these tools are listed in
[domain-contracts.md](domain-contracts.md).

## The `dambreak_val2d` template

The only template so far; templates are data (one dict entry), so more layouts
can be added cheaply. The defaults reproduce the official validation layout
(Koshizuka & Oka 1996 family):

| Parameter | Default |
| --- | --- |
| `dp` | 0.01 m |
| column | 1.0 m × 2.0 m at the origin |
| tank | 4.0 m × 3.0 m, open top |
| margins | `x_min`/`z_min` 1.0 m, `x_max`/`z_max` 0.5 m (splash headroom) → domain (-1, -1)…(4.5, 3.5) |
| `gravity` / `rhop0` / `cfl` / `visco` | 9.81 m/s² / 1000 kg/m³ / 0.2 / 0.02 |
| `time_max` / `time_out` | 2 s / 0.01 s |
| `obstacle` | none |
| gauges | `Swl_x02` (vertical, x = 0.2), `Swl_z003` (horizontal, z = 0.03) |

GenCase-measured baseline counts: fluid 20,000 + bound 1,001 = **21,001**.

## Override vocabulary (full table)

Unknown keys are rejected with the vocabulary listed. All checks below report
`DSPH_BAD_INPUT`.

| Key | Shape | Meaning and checks |
| --- | --- | --- |
| `dp` | number > 0 | particle spacing (m); particle counts scale as dp⁻² |
| `column_length`, `column_height` | > 0 | water column at the origin; each ≤ the matching tank dimension and ≥ dp |
| `tank_length`, `tank_height` | > 0 | open-top tank around it; ≥ dp |
| `obstacle` | `{"x", "width", "height"}` or `null` | solid box standing on the tank floor, downstream of the column: `x` ≥ `column_length` (inside the initial water column is unsupported), `x`+`width` ≤ `tank_length`, `height` ≤ `tank_height`, `width`/`height` ≥ dp |
| `margins` | `{"x_min", "z_min", "x_max", "z_max"}`, all > 0 | domain = tank + margins; merges key-by-key with the template margins |
| `domain` | `{"pointmin": [x, z], "pointmax": [x, z]}` | explicit domain box, overrides margins; must **strictly** contain the geometry (see guard 1) |
| `time_max`, `time_out` | > 0 | TimeMax / TimeOut (s); `0 < time_out ≤ time_max` |
| `gravity` | > 0 | m/s², emitted as negative z |
| `rhop0` | integer > 0 | reference density (kg/m³) |
| `cfl` | in (0, 1] | CFL coefficient |
| `visco` | ≥ 0 | artificial viscosity value |
| `gauges` | list of gauge objects | SWL probes, see below |

Gauge objects: `{"type": "vertical", "x", "z_top"?, "name"?}` or
`{"type": "horizontal", "z", "x_end"?, "name"?}` — cross-keys are rejected
(a vertical gauge takes `x`/`z_top`, not `z`/`x_end`, and vice versa).
Vertical `x` must lie in `[0, tank_length]`; horizontal `z` in
`[0, tank_height]`. `z_top` defaults to `column_height + 0.1` and must sit
above the column to see the free surface; `x_end` defaults to
`tank_length + 0.05` and must be > 0. Auto-names are `Swl_x<tag>` /
`Swl_z<tag>` (coordinate with `.`→`p`, `-`→`m`); explicit duplicate names are
an error, auto-generated collisions get a `_` suffix until unique.

Validation runs in two passes:

1. **Static, pure-parameter pass** — unknown keys, types, standalone ranges.
   This runs *before any filesystem access* (project iron rule), so bad
   numbers report as `DSPH_BAD_INPUT` even when the output path is unwritable
   or the case file does not exist.
2. **Merged-parameter pass** — relative checks the static pass cannot see:
   geometry containment, domain containment, obstacle placement, gauge
   placement, time ordering. `edit_case` re-runs it on the merged case.

## The three guards

Three validation rules come straight from measured GenCase behaviour:

1. **Anti silent-clipping.** Geometry outside the domain box is silently
   clipped (exit 0, no warning), and a domain boundary flush with the tank
   also drops the boundary lattice row. So margins must be positive and
   explicit `domain` overrides are containment-checked —
   `xmin < 0 < … < xmax > tank_length`, `zmax > tank_height`, equality
   rejected — otherwise `DSPH_BAD_INPUT`.
2. **`<setdrawmode mode="full"/>` is always emitted.** Without it the fluid
   loses a lattice row: 20,000 → 19,701 particles.
3. **2D plane crossing.** 2D means y pinned to 0 in the XZ plane, and every
   emitted box crosses y = 0 (spanning y in [-1, 1]); geometry missing the
   plane yields zero particles, silently.

## Rendering details

- Fixed execution parameters (the validation layout): Verlet
  (`StepAlgorithm 1`, `VerletSteps 40`), Wendland kernel (`Kernel 2`),
  artificial viscosity (`ViscoTreatment 1`, value = the `visco` override),
  Fourtakas density diffusion (`DensityDT 2`, value 0.1), no shifting;
  `TimeMax`/`TimeOut` from the overrides.
- **MK numbering.** The fluid is `setmkfluid mk="0"`; the tank is
  `setmkbound mk="0"`; the obstacle is `setmkbound mk="1"` (under
  `<mkconfig boundcount="240" fluidcount="9"/>`). The obstacle's mk resolves
  to absolute **MKBound 11** in the solver and post-processing — filter it
  with `MeasureTool -onlymk:11`, target gauge forces at `mkbound=11`.
  Results carry this as `obstacle_mk_note`.
- SWL gauges render as `<swl name=…>` entries: vertical gauges probe
  z from -0.05 to `z_top` at fixed x; horizontal gauges probe x from -0.05 to
  `x_end` at fixed z, with `computedt`/`outputdt` 0.005 and `pointdp
  coefdp="0.5"`.
- Number formatting is compact fixed-point (no scientific notation).

## Particle estimates

Every `create_case` / `edit_case` / `describe_case` result carries a
`particle_estimate` (`fluid` / `bound` / `total` / per-axis counts / `note`)
computed with lattice arithmetic measured against GenCase v5.4:

- fluid box: `round(L/dp)` per axis, multiplied;
- open-top tank shell: `(round(L/dp)+1) + 2·round(H/dp)` — the bottom row
  plus both walls (baseline: 401 + 2·300 = 1,001);
- solid obstacle: `(round(w/dp)+1)·(round(h/dp)+1)` added, its floor row of
  `round(w/dp)+1` points removed (the obstacle replaces bottom particles):
  0.1 m × 0.1 m at dp 0.01 → +121 − 11 = +110, so bound 1,001 → 1,111
  (990 + 121).

The note shipped with every estimate: GenCase's own particle summary is
authoritative; sizes that are not integer multiples of dp (or an obstacle
touching the water column edge) can shift counts by a few particles.

### Measured anchors (GenCase v5.4.354)

Eight parametrized anchors, verified as estimate == real GenCase counts:

| Overrides | fluid | bound | total |
| --- | --- | --- | --- |
| — (template defaults) | 20,000 | 1,001 | 21,001 |
| `column_height: 1.5` | 15,000 | 1,001 | 16,001 |
| `column_height: 1.5` + obstacle x=2.5, 0.1×0.1 | 15,000 | 1,111 (990 + 121) | 16,111 |
| `dp: 0.003` | 222,111 | 3,334 | 225,445 |
| `dp: 0.008` | 31,250 | 1,251 | 32,501 |
| `column_height: 1.5` + obstacle x=3.0, 0.2×0.05 | 15,000 | 1,106 | 16,106 |
| `column_height: 1.5` + obstacle x=3.0, 0.33×0.07 | 15,000 | 1,239 | 16,239 |
| non-integer sizes: column 0.75×1.23, tank 3.55×2.7, obstacle x=2.1, 0.07×0.13 | 9,225 | 1,000 | 10,225 |

Three further measured checks bring the total to eleven:

- editing the official example to `dp: 0.02` → fluid 5,000 + bound 501 =
  total 5,501 (estimate == GenCase);
- dropping `<setdrawmode mode="full"/>` → fluid drops 20,000 → 19,701
  (guard 2);
- the solver-marked closed-loop test: `create_case` → real `gencase` on the
  baseline and the obstacle variant, GenCase counts equal the estimates.

A programmatic demo lives in
[`examples/create_case_demo.py`](../examples/create_case_demo.py).

## `edit_case` and `describe_case`

`edit_case` parses an existing `*_Def.xml`, applies the same override
vocabulary on top of the parsed parameters, re-validates, and writes (in
place, or to `save_path`; `create_case` refuses to overwrite an existing file
and `edit_case` refuses an existing `save_path`; a missing `.xml` suffix is
appended). `describe_case` is the read-only summary of the same parse.

Parsing notes: XML comments are stripped first, because DualSPHysics files
often carry `--` inside comments (illegal XML that GenCase tolerates). The
parser accepts only the dambreak_val2d vocabulary — one fluid box, one tank
box, at most one obstacle box, vertical/horizontal gauges — and rejects 3D
domains (`pointmin.y != pointmax.y`), non-family layouts and diagonal gauges
with `DSPH_BAD_INPUT`. A domain not expressible as tank + positive margins is
kept verbatim so the merged validation rejects it, by design.

## A worked variant

1.5 m column plus a downstream obstacle:

```
create_case(out="cases/CaseObst_Def.xml",
            overrides={"column_height": 1.5,
                       "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1}})
  -> particle_estimate: fluid=15000 bound=1111 total=16111
describe_case(path="cases/CaseObst_Def.xml")     # self-check the summary
gencase(xml_path="cases/CaseObst_Def.xml")
  -> particle_counts: total=16111 fluid=15000 bound=1111   # estimate == reality
edit_case(path="cases/CaseObst_Def.xml", overrides={"time_max": 3.0})
```

`create_case` also returns `applied` (the sorted override keys), the merged
case summary, the `obstacle_mk_note` when an obstacle is present, and a
`next_step` pointer to `gencase`.
