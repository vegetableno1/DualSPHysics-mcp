# DualSPHysics domain contracts

Behaviours of the wrapped DualSPHysics tools that are not obvious from their
`-h` output. The code in this repository depends on each of them; they were
established by running the tools and reading their output (and, where noted,
their source). The README carries only a summary — this file is the full set.

Related: [case-generation.md](case-generation.md) documents how
`create_case` / `edit_case` / `describe_case` turn these contracts into
validation guards.

## Solver output (`Run.out`)

### 1. Row formats differ across solver versions

v5.0/v5.2 print 6 columns:

```
Part_0001  0.010016  314  314  494.50  21-06-2022 22:48:14
```

v5.4 prints 8 columns, with a plain `%05d` part number (no `Part_` prefix)
and **thousands separators** in the counters:

```
00001  0.010017  314  314  21,001  2,736  216.96  <date> <time>
```

The `runout` parser accepts both; progress = `PartTime / TimeMax`.

### 2. `Time/Sec` is not steps/second

From the solver source (`JSph::SaveData`): `Time/Sec` is wall-clock seconds
per simulated second for the last PART. The last two tokens of a row are the
solver's own projected **finish date-time** (its ETA estimate). `job_status`
surfaces both verbatim.

### 3. Completion and failure markers

Completion is `Finished execution (code=N).` (from `main.cpp`); N = 0 means
success. Fatal errors print `*** Exception(exc): ...` lines first.

## GenCase

### 4. Argument convention, defaults, and its own log

GenCase takes path bases *without* `.xml` (`GenCase CaseX_Def OUT/CaseX`).
This wrapper accepts both forms and strips the suffix; the default output
name drops `_Def`, and the default output directory is `<name>_out`. GenCase
writes its own log next to the case (`CaseX.out`), *not* `Run.out`.

### 5. Geometry outside the domain box is silently clipped

Clipping exits 0 with no warning, and a domain boundary flush with the
geometry also drops the boundary lattice row. The wrapper therefore derives
the domain from the tank plus strictly positive margins and
containment-checks explicit `domain` overrides (`xmin < 0`, `zmin < 0`,
`xmax > tank_length`, `zmax > tank_height`; equality rejected) — violations
report `DSPH_BAD_INPUT` instead of a silently clipped case. See
[case-generation.md](case-generation.md#the-three-guards).

### 6. `<setdrawmode mode="full"/>` is mandatory

Without it the fluid loses a lattice row at the boundary: a 20,000-particle
fluid renders as 19,701. The renderer always emits it first in the
`mainlist`.

### 7. 2D cases pin y to one plane

A 2D case is declared by giving the domain definition
`pointmin y == pointmax y` (this vocabulary pins both to 0); the geometry's
y extent is clamped to that plane. Every box must cross y = 0 — the emitted
geometry spans y in [-1, 1] — because geometry missing the plane yields zero
particles, silently.

### 8. Particle counts come from the VTK headers

GenCase run with `-save:all` writes VTK headers whose `POINTS <n> float` line
is ASCII even when the payload is binary; the console line
`Total particles: 21,001 (bound=1001 ... fluid=20000)` is the fallback.
Beware look-alikes when parsing (e.g. `MassFluid=[0.1]`).

## MeasureTool

### 9. DualSPHysics CSVs default to semicolon separators

The default is semicolon — `-csvsep:0` from `DsphConfig.xml`. `measure_tool`
always appends `-csvsep:1` (comma) unless the caller passes their own
`-csvsep`; `validate_dambreak` sniffs both.

### 10. Points-file quirks

Points files reject leading `#` comment lines ("There are not valid points in
file"); only inline trailing comments are safe — see
[`examples/dambreak_val2d/points_damtip.txt`](../examples/dambreak_val2d/points_damtip.txt).
`-points` paths are resolved against the tool's working directory (the job
dir), so the wrapper absolutises them first. Errors print on **stdout**, not
stderr.

### 11. `-savecsv <prefix>` writes transposed, per-variable CSVs

One file per variable: `<prefix>_<Var>.csv`, with a transposed header —
PosX/PosY/PosZ rows, a `Part,Time [s],Var_0,...` header row, then
`part,time,values...` data rows. `validate_dambreak` reads that layout (and a
generic time-first one).

## Environment and toolchain builds

### 12. The solver needs its sibling `.so` files

`libChronoEngine.so`, `libdsphchrono.so`, etc. must sit next to the solver.
Some installs set rpath, some don't — `run_case` therefore always prepends
the solver's directory to `LD_LIBRARY_PATH`.

### 13. Version/feature probes can lie about success, and WaveGen may be absent

`-ver` / `-info` may exit non-zero even while printing a correct banner
(GenCase's `-ver` exits 1). `check_environment` trusts the printed banner,
not the exit code. Separately, solvers built with `-DDISABLE_WAVEGEN` —
common when building from the GitHub source, which lacks `libjwavegen_64` —
cannot run wave/wavemaker cases; `check_environment` reads the solver's
`-info` JSON and flags it. Dam-break and other gravity-driven cases are
unaffected.

### 14. Restart / cancel are not implemented

There is no wrapper-level restart or cancellation. The interface is the
`extra_args` passthrough (e.g. `-partbegin:<n>`) — everything the solver CLI
accepts can be passed.
