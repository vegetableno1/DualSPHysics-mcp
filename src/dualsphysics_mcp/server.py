"""MCP server (stdio transport): check_environment / gencase / run_case /
job_status / partvtk / measure_tool / validate_dambreak.

Stdio is the only transport. stdout belongs to the protocol — nothing in this
module may print to stdout.

Tool-output design:
- every tool returns a typed pydantic model (structured output);
- long work (the solver) is a background job: run_case returns a job_id
  immediately, job_status parses the solver's own Run.out for progress
  (t/tmax, percent, steps, wall-seconds per simulated second, the solver's
  projected finish time);
- bulky artefacts (VTK/CSV/Run.out) stay on disk; tool responses carry
  absolute paths and short tails only;
- domain errors surface as ``ToolError("<CODE>: <message>")`` with stable
  codes (DSPH_TOOL_MISSING, DSPH_BAD_INPUT, DSPH_RUN_FAILED, DSPH_JOB_NOT_FOUND).

Workflow for the 2D dam-break validation showcase::

    check_environment()
    gencase(xml_path="examples/dambreak_val2d/CaseDambreakVal2D_Def.xml")
    run_case(case_path="<out>/CaseDambreakVal2D")       # -> job_id
    job_status(job_id)                                  # poll to 100%
    partvtk(job_id=job_id)
    measure_tool(job_id=job_id, points_file="examples/dambreak_val2d/points_damtip.txt")
    validate_dambreak(csv_path="<measure csv>", points_file=".../points_damtip.txt")
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from . import __version__
from .errors import DualSphysError
from .tools import environment, postprocess, runner, validate
from .tools import gencase as gencase_mod

mcp = MCPServer("dualsphysics-mcp")


# ---------------------------------------------------------------------------
# Tool result models
# ---------------------------------------------------------------------------


class ToolStatus(BaseModel):
    name: str = Field(description="Tool key: gencase | solver | partvtk | measuretool")
    role: str
    env_var: str
    path: str | None = Field(default=None, description="Resolved absolute path")
    found: bool
    source: str = Field(description="env | PATH | scan | missing")
    version: str | None = None
    install_hint: str | None = None
    notes: str | None = None
    features: dict[str, Any] | None = Field(
        default=None, description="Solver -info features (CPU/GPU/WaveGen/...)"
    )


class EnvironmentReport(BaseModel):
    server_version: str
    tools: list[ToolStatus]
    omp_threads: int
    jobs_dir: str
    jobs_dir_exists: bool
    ready: bool = Field(description="True when all four tool paths were found")
    missing: list[str]
    hint: str


class VtkFileInfo(BaseModel):
    path: str
    points: int | None = Field(default=None, description="Particles in the VTK file")


class GencaseResult(BaseModel):
    returncode: int
    command: list[str]
    out_xml: str
    out_bi4: str
    bi4_exists: bool
    vtk_files: dict[str, VtkFileInfo]
    particle_counts: dict[str, int | None] = Field(
        description="total/fluid/bound particle counts (None = not reported)"
    )
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_s: float
    next_step: str


class JobStarted(BaseModel):
    job_id: str
    state: str
    pid: int
    returncode: int | None = None
    command: list[str]
    case: str
    dirout: str
    omp_threads: str
    started_at: str
    finished_at: str | None = None
    data_dir: str = Field(description="Where Part_*.bi4 files appear")
    run_out: str = Field(description="Progress log parsed by job_status")
    stdout_log: str
    stderr_log: str
    stderr_tail: str = ""
    poll_hint: str
    restart_note: str


class ParticleCounts(BaseModel):
    loaded: int | None = Field(description="Loaded particles (Run.out)")
    total_case: int | None = Field(description="CaseNp")
    fluid: int | None = Field(description="CaseNfluid")
    bound: int | None = Field(description="CaseNbound")
    current_part: int | None = Field(description="Particles in the last PART (v5.4)")


class JobStatus(BaseModel):
    job_id: str
    state: str = Field(description="running | succeeded | failed | finished")
    pid: int | None = None
    returncode: int | None = None
    command: list[str] | None = None
    dirout: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    run_out_exists: bool
    current_time: float | None = Field(description="Simulated time of last PART (s)")
    tmax: float | None = None
    percent: float | None = None
    last_part: int | None = None
    total_steps: int | None = None
    steps_last_part: int | None = None
    wall_sec_per_sim_sec: float | None = Field(
        description="Solver Time/Sec column: wall-clock s per simulated s"
    )
    wall_sec_per_sim_sec_note: str = ""
    eta: str | None = Field(description="Solver's projected finish date-time")
    eta_note: str = ""
    particles: ParticleCounts
    exception: str | None = None
    run_out_tail: str = ""
    poll_hint: str | None = None


class PostprocessResult(BaseModel):
    returncode: int
    command: list[str]
    output_files: list[str] = Field(default_factory=list)
    file_count: int = 0
    csv_files: list[str] = Field(default_factory=list)
    vtk_files: list[str] = Field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_s: float = 0.0
    next_step: str | None = None


class ValidationSample(BaseModel):
    time_s: float
    sim_front_m: float
    exp_front_m: float
    error_m: float
    error_pct_of_column: float


class ValidationResult(BaseModel):
    experiment: str
    experiment_reference: str
    n_samples: int
    threshold: float
    column_length_m: float
    mae_m: float
    rmse_m: float
    max_abs_error_m: float
    mae_pct_of_column: float
    front_series: list[ValidationSample]
    impact_time_s: float | None = Field(
        default=None,
        description="When the front plateaued at the tank end (wall impact), if it did",
    )
    notes: str | None = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def check_environment() -> EnvironmentReport:
    """Probe the DualSPHysics toolchain: paths, versions, solver features.

    Reports for GenCase / CPU solver / PartVTK / MeasureTool whether each
    binary was found (env var, PATH or conventional install dirs), its version,
    and the solver's feature flags (a build without WaveGen cannot run wave
    cases). Call this first; missing tools come with install hints.
    """
    report = environment.check_environment()
    report["server_version"] = __version__
    return EnvironmentReport(**report)


@mcp.tool()
def gencase(
    xml_path: str,
    output_dir: str | None = None,
    out_name: str | None = None,
    save_modes: str = "all",
    extra_args: list[str] | None = None,
) -> GencaseResult:
    """Run GenCase: case XML -> Case.xml + Case.bi4 (+ preview VTK files).

    Args:
        xml_path: Path to the *_Def.xml case definition (with or without .xml).
        output_dir: Output directory (default: <name>_out next to the XML).
        out_name: Output case name (default: XML stem minus _Def).
        save_modes: GenCase -save value (default "all" = bi4 + preview VTKs).
        extra_args: Extra GenCase flags (e.g. ["-dp:0.02"]).
    """
    try:
        result = gencase_mod.run_gencase(xml_path, output_dir, out_name, save_modes, extra_args)
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return GencaseResult(**result)


@mcp.tool()
def run_case(
    case_path: str,
    dirout: str | None = None,
    omp_threads: int | None = None,
    tmax: float | None = None,
    tout: float | None = None,
    extra_args: list[str] | None = None,
) -> JobStarted:
    """Start the CPU solver in the BACKGROUND and return a job_id.

    The case must have been processed by gencase first (Case.xml + Case.bi4).
    Progress is tracked with job_status; outputs land in the job directory
    (Run.out, data/Part_*.bi4). Restart/cancel are not implemented; solver
    flags such as -partbegin:<n> can be passed via extra_args.

    Args:
        case_path: Processed case base (gencase out_xml, with/without .xml).
        dirout: Output directory (default: jobs/<job_id>).
        omp_threads: OpenMP threads (default: DSPH_OMP_THREADS or all cores).
        tmax: Override TimeMax (seconds of simulated time).
        tout: Override TimeOut (seconds between Part files).
        extra_args: Extra solver flags (e.g. ["-svres"]).
    """
    try:
        result = runner.start_case(case_path, dirout, omp_threads, tmax, tout, extra_args)
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return JobStarted(**result)


@mcp.tool()
def job_status(job_id: str) -> JobStatus:
    """Poll a background solver job: state + Run.out progress digest.

    Returns the running state (plus pid/returncode), simulated time vs tmax,
    percent, step counters, the solver's Time/Sec throughput (wall-clock
    seconds per simulated second), the solver's own projected finish time,
    particle counts and the Run.out tail.
    """
    try:
        result = runner.get_status(job_id)
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return JobStatus(**result)


@mcp.tool()
def partvtk(
    job_id: str | None = None,
    dirdata: str | None = None,
    savevtk: str = "particles/PartFluid",
    onlytype: str = "-all,fluid",
    variables: str | None = None,
    first: int | None = None,
    last: int | None = None,
    extra_args: list[str] | None = None,
) -> PostprocessResult:
    """PartVTK: convert Part_*.bi4 particle files to VTK for visualisation.

    Args:
        job_id: Job whose data/ directory holds the Part_*.bi4 files.
        dirdata: Explicit particle data directory (alternative to job_id).
        savevtk: Output prefix (default particles/PartFluid inside the job).
        onlytype: Particle filter (default fluid only).
        variables: Variables to store, e.g. "+idp,+vel,+rhop,+press".
        first/last: Part index range.
        extra_args: Extra PartVTK flags.
    """
    try:
        result = postprocess.run_partvtk(
            dirdata=dirdata,
            job_id=job_id,
            savevtk=savevtk,
            onlytype=onlytype,
            variables=variables,
            first=first,
            last=last,
            extra_args=extra_args,
        )
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return PostprocessResult(**result)


@mcp.tool()
def measure_tool(
    job_id: str | None = None,
    dirdata: str | None = None,
    points_file: str | None = None,
    pointsdef: str | None = None,
    variables: str = "-all,rhop",
    onlytype: str = "-all,+fluid",
    savecsv: str = "measure/damtip",
    savevtk: str | None = None,
    extra_args: list[str] | None = None,
) -> PostprocessResult:
    """MeasureTool: interpolate SPH values at points -> CSV time series.

    For the dam-break validation use a horizontal line of points just above
    the bottom (examples/dambreak_val2d/points_damtip.txt) with density
    (default -vars:-all,rhop): points beyond the surge front read 0, so the
    furthest wet point is the front position that validate_dambreak compares
    against the experiment.

    Args:
        job_id: Job whose data/ directory holds the Part_*.bi4 files.
        dirdata: Explicit particle data directory (alternative to job_id).
        points_file: MeasureTool points file (POINTS/POINTSLIST/POINTSENDLIST).
        pointsdef: Inline points definition, e.g. "ptels[x=0:0.01:4,z=0.03]".
        variables: Variables to interpolate (default density only).
        onlytype: Particle filter (default fluid only).
        savecsv: CSV output prefix (default measure/damtip inside the job).
        savevtk: Optional VTK prefix for the measuring points.
        extra_args: Extra MeasureTool flags (a -csvsep here overrides ours).
    """
    try:
        result = postprocess.run_measure_tool(
            dirdata=dirdata,
            job_id=job_id,
            points_file=points_file,
            pointsdef=pointsdef,
            variables=variables,
            onlytype=onlytype,
            savecsv=savecsv,
            savevtk=savevtk,
            extra_args=extra_args,
        )
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return PostprocessResult(**result)


@mcp.tool()
def validate_dambreak(
    csv_path: str,
    points: list[list[float]] | None = None,
    points_file: str | None = None,
    threshold: float = 500.0,
    column_length: float = 1.0,
    experiment: str = "koshizuka1996",
    max_time: float | None = None,
) -> ValidationResult:
    """Validate a 2D dam-break run: dam-tip front vs Koshizuka & Oka (1996).

    Reads the CSV produced by measure_tool with the SAME points definition
    (columns map to points by order), reconstructs the front position per
    output time (furthest point above the wetness threshold) and reports
    per-time errors plus MAE / RMSE / max error in metres and as % of the
    column length.

    Args:
        csv_path: MeasureTool time-history CSV.
        points: Points [[x, y, z], ...] in CSV column order.
        points_file: The points file used for the measurement.
        threshold: Wetness threshold (500 kg/m^3 for rhop; ~0.05 for vel).
        column_length: Water column length a in metres (normalisation).
        experiment: Embedded experiment (koshizuka1996).
        max_time: Ignore simulated samples beyond this time (s).
    """
    try:
        result = validate.validate_dambreak(
            csv_path,
            points=points,
            points_file=points_file,
            threshold=threshold,
            column_length=column_length,
            experiment=experiment,
            max_time=max_time,
        )
    except DualSphysError as exc:
        raise ToolError(str(exc)) from exc
    return ValidationResult(**result)


def main() -> None:
    """Console entry point (``dualsphysics-mcp``); stdio is the default transport."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
