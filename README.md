# DualSPHysics MCP

<!-- mcp-name: io.github.vegetableno1/dualsphysics -->

**Language:** English | [中文](README.zh-CN.md)

[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/vegetableno1/dualsphysics-mcp)
[![vegetableno1/DualSPHysics-mcp MCP server](https://glama.ai/mcp/servers/vegetableno1/DualSPHysics-mcp/badges/score.svg)](https://glama.ai/mcp/servers/vegetableno1/DualSPHysics-mcp)
[![CI](https://github.com/vegetableno1/DualSPHysics-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vegetableno1/DualSPHysics-mcp/actions/workflows/ci.yml)

DualSPHysics MCP wraps a local [DualSPHysics](https://dual.sphysics.org/) SPH
fluid-solver toolchain as ten [Model Context Protocol](https://modelcontextprotocol.io/)
tools: case **creation** without hand-writing XML (`create_case` / `edit_case` /
`describe_case`, pure Python with strong validation), case pre-processing
(GenCase), background CPU simulation with progress parsing, post-processing
(PartVTK / MeasureTool) and a quantitative dam-break validation against the
Koshizuka & Oka (1996) experiment. The intended user is an agent (or a human
driving an MCP client) that needs to *design*, run and *verify* SPH
simulations, not just stare at them.

The server only launches the DualSPHysics command-line tools as subprocesses.
DualSPHysics itself is **not** distributed here: it is free software under
**LGPL-2.1-or-later**, and because this package neither links nor redistributes
its code or binaries, the MIT licence of this repository carries no additional
obligations. If you use DualSPHysics in published work, cite
*Dominguez et al. (2022), "DualSPHysics: from fluid dynamics to multiphysics
problems", Computational Particle Mechanics 9:867–895,
[doi:10.1007/s40571-021-00404-2](https://doi.org/10.1007/s40571-021-00404-2).*

## Tools

| Tool | Purpose |
| --- | --- |
| `check_environment` | Probe GenCase / solver / PartVTK / MeasureTool: resolved path, provenance (env var, PATH, scan), version, solver feature flags (e.g. builds without WaveGen); install hints when missing. |
| `create_case` | Design a case `*_Def.xml` from a template plus overrides — no XML hand-writing, no solver needed. 2D dam-break family: water column, tank, downstream obstacle, dp, domain margins, TimeMax/TimeOut, SWL gauges. Returns a lattice-arithmetic particle estimate for self-checking. |
| `edit_case` | Apply the same override vocabulary to an existing `*_Def.xml` (in place or to a `save_path`), re-validating the merged case. |
| `describe_case` | Read-only summary of a `*_Def.xml`: dp, domain, column/tank/obstacle, timing, gauges, particle estimate — the agent self-check loop. |
| `gencase` | Run GenCase: case `*_Def.xml` → `Case.xml` + `Case.bi4` (+ preview VTK). Returns particle counts (total/fluid/bound) parsed from VTK headers and the console. |
| `run_case` | Start the CPU solver **in the background** (jobs/ directory, `status.json`, `Run.out`, `data/Part_*.bi4`) and return a `job_id` immediately. |
| `job_status` | Poll a job: state, `t`/`tmax`, percent, step counters, the solver's `Time/Sec` throughput and its own projected finish time, particle counts, `Run.out` tail. |
| `partvtk` | PartVTK: `Part_*.bi4` → VTK particle files (ParaView-ready), filters and variables passed through. |
| `measure_tool` | MeasureTool: SPH interpolation at points → CSV time series (`-csvsep:1` enforced so parsing is deterministic). |
| `validate_dambreak` | Pure-Python: reconstruct the dam-tip surge front from the MeasureTool CSV and compare against the embedded Koshizuka & Oka (1996) series; per-time errors + MAE/RMSE/max. |

Workflow: `check_environment` → `create_case` (design) → `describe_case`
(self-check) → `gencase` (discretise, real counts) → `run_case` → poll
`job_status` → `partvtk` (visual) + `measure_tool` (quantitative) →
`validate_dambreak`.

## Case creation (design experiments without XML)

`create_case` / `edit_case` / `describe_case` cover the "design" stage: the
agent describes a 2D dam-break-family experiment in structured parameters and
the server renders a GenCase `*_Def.xml`. The `dambreak_val2d` template
encodes the official validation layout (dp = 0.01 m, 1 m × 2 m column, 4 m × 3 m
tank, TimeMax = 2 s, gauges at x = 0.2 and z = 0.03); templates are data, so
more layouts can be added cheaply. Override keys (unknown keys are rejected
with the vocabulary listed):

| Key | Meaning |
| --- | --- |
| `dp` | particle spacing (m); counts scale as dp⁻² |
| `column_length`, `column_height` | water column at the origin |
| `tank_length`, `tank_height` | open-top tank around it |
| `obstacle` | `{"x", "width", "height"}` solid box on the tank floor (downstream of the column), or `null` |
| `margins` | domain = tank + margins (`x_min`/`z_min`/`x_max`/`z_max`, defaults 1/1/0.5/0.5 m splash headroom) |
| `domain` | explicit `{"pointmin": [x, z], "pointmax": [x, z]}` box (overrides margins) |
| `time_max`, `time_out` | TimeMax / TimeOut (s) |
| `gravity`, `rhop0`, `cfl`, `visco` | physics constants |
| `gauges` | list of `{"type": "vertical", "x", "z_top"?}` / `{"type": "horizontal", "z", "x_end"?}` SWL probes |

Three guards come straight from measured GenCase behaviour: geometry outside
the domain box is **silently clipped** (exit 0), so the domain must strictly
contain the tank — margins must be positive and explicit `domain` overrides
are containment-checked (`DSPH_BAD_INPUT` otherwise); `<setdrawmode
mode="full"/>` is always emitted (without it the fluid loses a lattice row:
20,000 → 19,701); 2D means y pinned to 0 in the XZ plane, and every box
crosses y = 0 (geometry missing the plane yields zero particles, silently).

Designing a variant — 1.5 m column plus a downstream obstacle:

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

| Case (measured with GenCase v5.4.354) | fluid | bound | total |
| --- | --- | --- | --- |
| baseline (template defaults) | 20,000 | 1,001 | 21,001 |
| column 1.5 m + obstacle 0.1×0.1 at x = 2.5 | 15,000 | 1,111 (990 + 121) | 16,111 |
| same with dp = 0.008 | 31,250 | 1,251 | 32,501 |

The estimate arithmetic (fluid `round(L/dp)` per axis, open-tank shell
`(round(L/dp)+1) + 2·round(H/dp)`, solid obstacle `(round(w/dp)+1)·(round(h/dp)+1)`
replacing its floor row) was verified against real GenCase runs on eight
configurations and is reported with a note that GenCase's own summary is
authoritative. A programmatic demo lives in
[`examples/create_case_demo.py`](examples/create_case_demo.py).

## Domain contracts (hard-won facts)

These are behaviours of the wrapped tools that are not obvious from their
`-h` output; the code depends on each of them.

- **Run.out row formats differ across versions.** v5.0/v5.2 print
  `Part_0001  0.010016  314  314  494.50  21-06-2022 22:48:14` (6 columns);
  v5.4 prints `00001  0.010017  314  314  21,001  2,736  216.96  <date> <time>`
  (8 columns, plain `%05d` part number, and **thousands separators** in the
  counters). The parser accepts both; progress = `PartTime / TimeMax`.
- **`Time/Sec` is not steps/second.** From solver source (`JSph::SaveData`):
  it is wall-clock seconds per simulated second for the last PART; the last
  two tokens of a row are the solver's own projected **finish date-time**
  (its ETA estimate). `job_status` surfaces both verbatim.
- **Completion marker** is `Finished execution (code=N).` (`main.cpp`); N=0
  success. Fatal errors print `*** Exception(exc): ...` lines first.
- **GenCase argument convention**: path bases *without* `.xml`
  (`GenCase CaseX_Def OUT/CaseX`). This wrapper accepts both forms and strips
  the suffix; default output name drops `_Def`, default output directory is
  `<name>_out`. GenCase writes its own log next to the case (`CaseX.out`),
  *not* `Run.out`.
- **Particle counts** come from the VTK headers GenCase writes with
  `-save:all` (`POINTS <n> float` is ASCII even when the payload is binary)
  with the console line
  `Total particles: 21,001 (bound=1001 ... fluid=20000)` as fallback — beware
  look-alikes (`MassFluid=[0.1]`) when parsing.
- **DualSPHysics CSVs default to semicolon separators** (`-csvsep:0` from
  `DsphConfig.xml`). `measure_tool` always appends `-csvsep:1` (comma) unless
  the caller passes their own `-csvsep`; `validate_dambreak` sniffs both.
- **MeasureTool points files reject leading `#` comment lines** ("There are
  not valid points in file"); only inline trailing comments are safe — see
  `examples/dambreak_val2d/points_damtip.txt`. `-points` paths are resolved
  against the tool's working directory (the job dir), so the wrapper
  absolutises them first. Errors print on **stdout**, not stderr.
- **MeasureTool `-savecsv <prefix>` writes `<prefix>_<Var>.csv`** (one file
  per variable) with a transposed header: PosX/PosY/PosZ rows, a
  `Part,Time [s],Var_0,...` header row, then `part,time,values...` data rows.
  `validate_dambreak` reads that layout (and a generic time-first one).
- **The solver needs its sibling `.so` files** (`libChronoEngine.so`,
  `libdsphchrono.so`). Some installs set rpath, some don't — `run_case`
  therefore always prepends the solver's directory to `LD_LIBRARY_PATH`.
- **2D cases** are declared by giving the domain definition
  `pointmin y == pointmax y`; the geometry's y extent is clamped to that plane.
- **Solvers built with `-DDISABLE_WAVEGEN`** (common when building from the
  GitHub source, which lacks `libjwavegen_64`) cannot run wave/wavemaker
  cases; `check_environment` reads the solver's `-info` JSON and flags it.
  Dam-break and other gravity-driven cases are unaffected.
- **`-ver` / `-info` may exit non-zero** even while printing a correct banner
  (GenCase `-ver` exits 1). `check_environment` trusts the printed banner, not
  the exit code.
- **Restart/cancel** are not implemented. The interface is the `extra_args`
  passthrough (e.g. `-partbegin:<n>`) — everything the solver CLI accepts.

## Install

Python 3.10+; the DualSPHysics binaries are discovered from environment
variables, PATH, or conventional install roots (`/opt`, `/usr/local`,
`~/softwares`, `~`, cwd → `<root>/DualSPHysics*/bin/linux`).

```bash
# server + dev tools
uv sync                 # or: python -m venv .venv && .venv/bin/pip install -e .
```

Getting the solver (the GitHub repository ships GenCase/PartVTK/MeasureTool
prebuilt but **not** the solver):

1. Full package from <https://dual.sphysics.org/downloads/> (browser
   download, includes everything), or
2. `git clone https://github.com/DualSPHysics/DualSPHysics` and build the CPU
   solver from source: `make -f Makefile_cpu` (g++ only, no CUDA needed),
   then copy the prebuilt tools from `bin/linux/`.

Example layout (also the one used in `.env.example` /
`examples/mcp_config.example.json`):

```
/home/<user>/softwares/DualSPHysics/bin/linux/
├── GenCase_linux64  DualSPHysics5.4CPU_linux64  PartVTK_linux64
└── MeasureTool_linux64  libChronoEngine.so  libdsphchrono.so ...
```

Configure via environment variables (no .env parsing; see `.env.example`):

```
DSPH_GENCASE=/home/<user>/softwares/DualSPHysics/bin/linux/GenCase_linux64
DSPH_SOLVER=/home/<user>/softwares/DualSPHysics/bin/linux/DualSPHysics5.4CPU_linux64
DSPH_PARTVTK=/home/<user>/softwares/DualSPHysics/bin/linux/PartVTK_linux64
DSPH_MEASURETOOL=/home/<user>/softwares/DualSPHysics/bin/linux/MeasureTool_linux64
DSPH_JOBS_DIR=/abs/path/to/jobs     # optional, default ./jobs
DSPH_OMP_THREADS=8                  # optional, default all cores
```

## Run

```bash
.venv/bin/python mcp_server.py    # hub-style entry point (stdio)
.venv/bin/dualsphysics-mcp        # console script
uvx dualsphysics-mcp              # once published to PyPI
```

Client registration templates (uvx and local-venv variants) are in
[`examples/mcp_config.example.json`](examples/mcp_config.example.json):

```json
{ "mcpServers": { "dualsphysics-mcp": { "command": "uvx", "args": ["dualsphysics-mcp"] } } }
```

```bash
claude mcp add dualsphysics-mcp -- /abs/repo/path/.venv/bin/python /abs/repo/path/mcp_server.py
```

## Example: 2D dam-break validation (Koshizuka & Oka 1996)

`examples/dambreak_val2d/` contains the showcase: a programmatically generated
2D dam-break case (dp = 0.01 m; 1 m × 2 m water column in a 4 m × 3 m tank;
TimeMax = 2 s, TimeOut = 0.01 s; Verlet/Wendland/artificial viscosity 0.02/
Fourtakas DDT 0.1 — the official validation layout, regenerated by
`gen_dambreak_val2d.py`, MIT), a MeasureTool points file tracing the surge
front (401 points along z = 0.03 m), and the digitised experimental series.

Reference numbers (measured with GenCase v5.4.354 / solver v5.4.355, 8 CPU
threads, i5-10210U):

| Quantity | Value |
| --- | --- |
| Total particles | **21,001** (fluid 20,000 + bound 1,001) |
| Part files | 201 (`Part_0000` … `Part_0200`) |
| Solver wall time (TimeMax = 2 s) | ≈ 14 min |
| Dam-tip MAE vs experiment (full window 0.09–0.75 s) | 0.220 m (22.0 % of the column) |
| Dam-tip MAE, early collapse (t ≤ 0.2 s) | ≈ ±0.01–0.16 m |
| Wall impact | front pins at 3.98 m at t ≈ 0.67 s |

The front extraction was cross-checked against the solver's own SWL gauge
(`GaugesSWL_Swl_z003.csv`, mean deviation 8 mm — within the 10 mm point
spacing), so the residual error against the experiment is SPH physics
(the DBC front slightly over-runs the experiment mid-collapse), not a
measurement artefact. The experimental series ends at X/a ≈ 4.13, beyond the
4 m tank, so the comparison saturates after wall impact —
`validate_dambreak` reports `impact_time_s` and says so in `notes`.

Agent transcript:

```
check_environment()                                  # all four tools found
gencase("examples/dambreak_val2d/CaseDambreakVal2D_Def.xml")
  -> particle_counts: total=21001 fluid=20000 bound=1001
run_case("<out>/CaseDambreakVal2D")                  # returns job_id
job_status(job_id)                                   # poll until percent=100
partvtk(job_id=job_id)                               # 201 fluid VTK files
measure_tool(job_id=job_id, points_file="examples/dambreak_val2d/points_damtip.txt")
validate_dambreak(csv_path="<measure csv>",
                  points_file="examples/dambreak_val2d/points_damtip.txt")
```

`validate_dambreak` reports per-time errors plus MAE / RMSE / max in metres
and as % of the 1 m column, the wall-impact time, and a note when the
comparison saturates.

## Tests

```bash
uv run pytest -q          # or: .venv/bin/python -m pytest
uv run ruff check .
```

The suite is green **without** any solver installed: tool discovery, command
construction, Run.out parsing (real v5.0/v5.4 log fixtures), validation math
(hand-computed synthetic CSVs), case generation (XML structure, override
validation, edit/describe round trips, particle-estimate anchors measured
against a real GenCase) and a stdio end-to-end test that spawns the server
and drives the case-creation tools with no toolchain at all. Tests marked
`solver` additionally exercise the real toolchain when one is installed
locally and auto-skip otherwise.

## Contents

```
DualSPHysics-mcp/
├── README.md, README.zh-CN.md     this file + Chinese translation
├── LICENSE                        MIT (this repository)
├── pyproject.toml                 hatchling, src layout, dualsphysics-mcp entry point
├── mcp_server.py                  stdio entry shim (hub convention)
├── .env.example                   DSPH_* variable template
├── src/dualsphysics_mcp/
│   ├── server.py                  MCPServer + the 10 tools (pydantic results)
│   ├── config.py                  env vars + tool discovery
│   ├── errors.py                  stable error codes
│   └── tools/
│       ├── environment.py         check_environment
│       ├── casegen.py             create_case / edit_case / describe_case
│       ├── gencase.py             gencase
│       ├── runner.py + runout.py  run_case / job_status (+ Run.out parser)
│       ├── postprocess.py         partvtk / measure_tool
│       └── validate.py            validate_dambreak (+ embedded experiment)
├── examples/
│   ├── dambreak_val2d/            case XML generator, points, experiment CSV
│   ├── create_case_demo.py        create_case baseline + variant demo
│   └── mcp_config.example.json    client registration templates
└── tests/                         pytest suite (solver tests auto-skip)
```

## Repository rules

Committed: source, tests, examples (case generator + measurement points +
digitised experiment with attribution), documentation, templates.
**Not** committed: DualSPHysics binaries or sources (LGPL work stays out),
virtual environments, `.env`, job outputs (`jobs/`), generated results
(`*_out/`, `Part_*.bi4`, VTK/CSV artefacts), caches, internal tooling
directories. Experimental values in `examples/dambreak_val2d/` are digitised
facts cited to Koshizuka & Oka (1996) as shipped with the DualSPHysics
examples; the generated case XML and all code here are original MIT content.
