"""partvtk / measure_tool: wrap the official post-processing binaries.

Contracts (doc/help/PartVTK_Help.out, doc/help/MeasureTool_Help.out):

* ``PartVTK -dirdata <dir> -savevtk <prefix> -onlytype:-all,fluid`` writes
  ``<prefix>_XXXX.vtk`` per part file.
* ``MeasureTool -dirdata <dir> -points <file>|-pointsdef:<def> -vars:...
  -savecsv <prefix>`` writes one CSV with the time history of the
  interpolated values (one column per point per variable).

DualSPHysics writes CSVs with semicolon separators by default
(``-csvsep:0``, from DsphConfig.xml); this wrapper always appends
``-csvsep:1`` (comma) so downstream parsing — including validate_dambreak —
is deterministic, unless the caller passes their own ``-csvsep``.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from .. import config
from ..errors import BadInputError, RunFailedError, ToolMissingError
from .runner import DATA_DIRNAME, _job_dir

POSTPROCESS_TIMEOUT_S = 3600.0


def _resolve_dirdata(
    dirdata: str | None,
    job_id: str | None,
    jobs_root: Path | None = None,
) -> Path:
    if (dirdata is None) == (job_id is None):
        raise BadInputError("pass exactly one of dirdata / job_id")
    if dirdata is not None:
        path = Path(dirdata).expanduser()
    else:
        assert job_id is not None
        path = _job_dir(job_id, jobs_root) / DATA_DIRNAME
    if not path.is_dir():
        raise BadInputError(f"particle data directory not found: {path} (run the solver first)")
    return path


def _run_tool(
    argv: list[str], exe: Path, cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=config.solver_runtime_env(exe),
        )
    except subprocess.TimeoutExpired as exc:
        raise RunFailedError(f"{argv[0]} timed out after {timeout:.0f}s") from exc


def build_partvtk_command(
    partvtk_exe: Path,
    dirdata: Path,
    savevtk: str,
    onlytype: str = "-all,fluid",
    variables: str | None = None,
    first: int | None = None,
    last: int | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Argv for PartVTK (filters and variable selection are passed through)."""
    argv = [str(partvtk_exe), "-dirdata", str(dirdata), "-savevtk", savevtk]
    if onlytype:
        argv.append(f"-onlytype:{onlytype}")
    if variables:
        argv.append(f"-vars:{variables}")
    if first is not None:
        argv.append(f"-first:{first}")
    if last is not None:
        argv.append(f"-last:{last}")
    if extra_args:
        argv.extend(extra_args)
    return argv


def run_partvtk(
    dirdata: str | None = None,
    job_id: str | None = None,
    savevtk: str = "particles/PartFluid",
    onlytype: str = "-all,fluid",
    variables: str | None = None,
    first: int | None = None,
    last: int | None = None,
    extra_args: list[str] | None = None,
    jobs_root: Path | None = None,
    timeout: float = POSTPROCESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Convert Part_*.bi4 to VTK files for visualisation (e.g. in ParaView)."""
    exe = config.tool_path("partvtk")
    if exe is None or not exe.is_file():
        raise ToolMissingError("PartVTK executable not found; set DSPH_PARTVTK")

    data_dir = _resolve_dirdata(dirdata, job_id, jobs_root)
    prefix = Path(savevtk)
    if not prefix.is_absolute():
        prefix = data_dir.parent / prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)

    argv = build_partvtk_command(
        exe, data_dir, str(prefix), onlytype, variables, first, last, extra_args
    )
    started = time.monotonic()
    proc = _run_tool(argv, exe, data_dir.parent, timeout)
    duration = round(time.monotonic() - started, 3)
    if proc.returncode != 0:
        raise RunFailedError(
            f"PartVTK exited with code {proc.returncode}; stderr tail: "
            f"{(proc.stderr or '')[-800:]!r}"
        )

    produced = sorted(str(p) for p in prefix.parent.glob(prefix.name + "*.vtk"))
    return {
        "returncode": proc.returncode,
        "command": argv,
        "output_files": produced,
        "file_count": len(produced),
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-800:],
        "duration_s": duration,
    }


def build_measure_command(
    measure_exe: Path,
    dirdata: Path,
    savecsv: str,
    points_file: str | None = None,
    pointsdef: str | None = None,
    variables: str = "-all,rhop",
    onlytype: str = "-all,+fluid",
    savevtk: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Argv for MeasureTool (exactly one of points_file / pointsdef)."""
    if (points_file is None) == (pointsdef is None):
        raise BadInputError("pass exactly one of points_file / pointsdef")
    argv = [str(measure_exe), "-dirdata", str(dirdata)]
    if points_file is not None:
        argv += ["-points", points_file]
    else:
        assert pointsdef is not None
        argv.append(f"-pointsdef:{pointsdef}")
    argv.append(f"-onlytype:{onlytype}")
    if variables:
        argv.append(f"-vars:{variables}")
    argv += ["-savecsv", savecsv]
    if savevtk:
        argv += ["-savevtk", savevtk]
    if extra_args:
        argv.extend(extra_args)
    else:
        extra_args = []
    if not any(arg.startswith("-csvsep") for arg in extra_args):
        argv.append("-csvsep:1")
    return argv


def run_measure_tool(
    dirdata: str | None = None,
    job_id: str | None = None,
    points_file: str | None = None,
    pointsdef: str | None = None,
    variables: str = "-all,rhop",
    onlytype: str = "-all,+fluid",
    savecsv: str = "measure/damtip",
    savevtk: str | None = None,
    extra_args: list[str] | None = None,
    jobs_root: Path | None = None,
    timeout: float = POSTPROCESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Interpolate SPH values at points -> CSV time series (for validation)."""
    exe = config.tool_path("measuretool")
    if exe is None or not exe.is_file():
        raise ToolMissingError("MeasureTool executable not found; set DSPH_MEASURETOOL")

    data_dir = _resolve_dirdata(dirdata, job_id, jobs_root)
    points_path: Path | None = None
    if points_file is not None:
        # The tool resolves -points relative to ITS cwd (the job dir), so the
        # caller's relative path must be absolutised against the caller first.
        points_path = Path(points_file).expanduser().resolve()
        if not points_path.is_file():
            raise BadInputError(f"points file not found: {points_path}")

    prefix = Path(savecsv)
    if not prefix.is_absolute():
        prefix = data_dir.parent / prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)

    vtk_prefix: Path | None = None
    if savevtk:
        vtk_prefix = Path(savevtk)
        if not vtk_prefix.is_absolute():
            vtk_prefix = data_dir.parent / vtk_prefix
        vtk_prefix.parent.mkdir(parents=True, exist_ok=True)

    argv = build_measure_command(
        exe,
        data_dir,
        str(prefix),
        points_file=str(points_path) if points_path else None,
        pointsdef=pointsdef,
        variables=variables,
        onlytype=onlytype,
        savevtk=str(vtk_prefix) if vtk_prefix else None,
        extra_args=extra_args,
    )
    started = time.monotonic()
    proc = _run_tool(argv, exe, data_dir.parent, timeout)
    duration = round(time.monotonic() - started, 3)
    if proc.returncode != 0:
        # MeasureTool prints its *** Exception text on stdout, not stderr.
        raise RunFailedError(
            f"MeasureTool exited with code {proc.returncode}; output tail: "
            f"{((proc.stderr or '') + (proc.stdout or ''))[-800:]!r}"
        )

    csv_files = sorted(str(p) for p in prefix.parent.glob(prefix.name + "*.csv"))
    vtk_files = (
        sorted(str(p) for p in vtk_prefix.parent.glob(vtk_prefix.name + "*.vtk"))
        if vtk_prefix
        else []
    )
    return {
        "returncode": proc.returncode,
        "command": argv,
        "csv_files": csv_files,
        "vtk_files": vtk_files,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-800:],
        "duration_s": duration,
        "next_step": (
            f'feed the csv to validate_dambreak (csv_path="{csv_files[0] if csv_files else ""}", '
            "same points) to compare against Koshizuka & Oka (1996)"
        ),
    }
