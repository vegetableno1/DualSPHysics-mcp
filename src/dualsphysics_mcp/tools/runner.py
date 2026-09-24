"""run_case / job_status: background solver jobs under ``jobs/<job_id>/``.

Long-task model (start -> poll -> fetch, hub LAMMPS convention):

* ``run_case`` spawns the CPU solver with :class:`subprocess.Popen`
  (``start_new_session=True`` so the job survives the tool call) and returns a
  ``job_id`` immediately. Job directory layout::

      jobs/<job_id>/status.json     state, pid, argv, timestamps
      jobs/<job_id>/Run.out         solver progress log (parsed by job_status)
      jobs/<job_id>/data/Part_*.bi4 particle snapshots
      jobs/<job_id>/solver_stdout.log / solver_stderr.log

* ``job_status`` merges three sources: the in-process Popen registry (same
  server session), ``status.json`` (cross-session) and the parsed ``Run.out``
  (t / tmax, percent, steps, wall-seconds per simulated second, the solver's
  own projected finish time).

Restart / cancellation are deliberately NOT implemented; power users can pass
solver flags (e.g. ``-partbegin:...``) through ``extra_args``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from ..errors import BadInputError, JobNotFoundError, ToolMissingError
from .gencase import append_suffix, strip_xml_suffix
from .runout import parse_run_out_file

STATUS_FILE = "status.json"
RUN_OUT = "Run.out"
STDOUT_LOG = "solver_stdout.log"
STDERR_LOG = "solver_stderr.log"
DATA_DIRNAME = "data"

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Live Popen handles for jobs started by THIS server process (bytes mode:
#: stdout/stderr stream to files, never to pipes).
_PROCS: dict[str, subprocess.Popen[bytes]] = {}

#: Grace period (seconds) after spawn to catch immediate startup failures.
STARTUP_GRACE_S = 0.75


def new_job_id() -> str:
    """Sortable, collision-safe id: ``job-YYYYmmdd-HHMMSS-xxxx``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"job-{stamp}-{secrets.token_hex(2)}"


def build_solver_command(
    solver_exe: Path,
    case_path: Path,
    dirout: Path,
    omp_threads: int | None = None,
    tmax: float | None = None,
    tout: float | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Argv for the CPU solver: ``<exe> <case_base> <dirout> -cpu -ompthreads:N``."""
    case_base = str(strip_xml_suffix(case_path))
    threads = omp_threads if omp_threads and omp_threads >= 1 else config.omp_threads()
    argv = [str(solver_exe), case_base, str(dirout), "-cpu", f"-ompthreads:{threads}"]
    if tmax is not None:
        argv.append(f"-tmax:{tmax}")
    if tout is not None:
        argv.append(f"-tout:{tout}")
    if extra_args:
        argv.extend(extra_args)
    return argv


def _write_status(job_dir: Path, status: dict[str, Any]) -> None:
    (job_dir / STATUS_FILE).write_text(json.dumps(status, indent=2), encoding="utf-8")


def _read_status(job_dir: Path) -> dict[str, Any]:
    path = job_dir / STATUS_FILE
    if not path.is_file():
        raise JobNotFoundError(
            f"job status file not found: {path} (unknown job id or pre-0.1 layout)"
        )
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - foreign owner, still running
        return True
    return True


def _tail(path: Path, chars: int = 1500) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-chars:]


def _finalise(
    status: dict[str, Any],
    job_dir: Path,
    returncode: int | None,
    run_out_code: int | None,
) -> dict[str, Any]:
    code = returncode if returncode is not None else run_out_code
    if code == 0:
        status["state"] = "succeeded"
    elif code is None:
        status["state"] = "finished"
    else:
        status["state"] = "failed"
    status["returncode"] = code
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_status(job_dir, status)
    return status


def start_case(
    case_path: str,
    dirout: str | None = None,
    omp_threads: int | None = None,
    tmax: float | None = None,
    tout: float | None = None,
    extra_args: list[str] | None = None,
    jobs_root: Path | None = None,
) -> dict[str, Any]:
    """Start the solver in the background; returns job_id + job layout paths."""
    # Input validation first: DSPH_BAD_INPUT wins over DSPH_TOOL_MISSING even
    # when no toolchain is installed anywhere (CI machines; spec error matrix).
    case = Path(case_path).expanduser().resolve()
    case_xml = case if case.name.endswith(".xml") else append_suffix(case, ".xml")
    if not case_xml.is_file():
        raise BadInputError(f"processed case XML not found: {case_xml} (run gencase first)")
    case_bi4 = append_suffix(strip_xml_suffix(case_xml), ".bi4")
    if not case_bi4.is_file():
        raise BadInputError(
            f"particle file not found: {case_bi4} (GenCase must produce it; re-run gencase)"
        )

    solver = config.tool_path("solver")
    if solver is None or not solver.is_file():
        raise ToolMissingError(
            "DualSPHysics CPU solver not found; set DSPH_SOLVER "
            "(build it with `make -f Makefile_cpu` from the official source, see "
            "check_environment)"
        )

    job_id = new_job_id()
    root = Path(jobs_root) if jobs_root else config.jobs_dir()
    job_dir = Path(dirout).expanduser() if dirout else root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    argv = build_solver_command(solver, case_xml, job_dir, omp_threads, tmax, tout, extra_args)
    env = config.solver_runtime_env(solver)
    threads = omp_threads if omp_threads and omp_threads >= 1 else config.omp_threads()
    env["OMP_NUM_THREADS"] = str(threads)

    stdout_path = job_dir / STDOUT_LOG
    stderr_path = job_dir / STDERR_LOG
    with stdout_path.open("wb") as out_handle, stderr_path.open("wb") as err_handle:
        proc = subprocess.Popen(
            argv,
            stdout=out_handle,
            stderr=err_handle,
            cwd=str(job_dir),
            env=env,
            start_new_session=True,
        )
    _PROCS[job_id] = proc

    status: dict[str, Any] = {
        "job_id": job_id,
        "state": "running",
        "pid": proc.pid,
        "returncode": None,
        "command": argv,
        "case": str(case_xml),
        "dirout": str(job_dir),
        "omp_threads": env["OMP_NUM_THREADS"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    _write_status(job_dir, status)

    # Catch immediate startup failures (bad flags, missing libs) before returning.
    time.sleep(STARTUP_GRACE_S)
    if proc.poll() is not None:
        status = _finalise(status, job_dir, proc.returncode, None)

    result = dict(status)
    result.update(
        {
            "data_dir": str(job_dir / DATA_DIRNAME),
            "run_out": str(job_dir / RUN_OUT),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "stderr_tail": _tail(stderr_path),
            "poll_hint": f'call job_status(job_id="{job_id}") to track progress',
            "restart_note": (
                "restart/cancel are not implemented; solver flags such as "
                "-partbegin:<n> can be passed via extra_args"
            ),
        }
    )
    return result


def _job_dir(job_id: str, jobs_root: Path | None = None) -> Path:
    if not _JOB_ID_RE.match(job_id):
        raise BadInputError(f"invalid job id: {job_id!r}")
    root = Path(jobs_root) if jobs_root else config.jobs_dir()
    return root / job_id


def get_status(job_id: str, jobs_root: Path | None = None) -> dict[str, Any]:
    """Merge status.json + live process state + parsed Run.out progress."""
    job_dir = _job_dir(job_id, jobs_root)
    status = _read_status(job_dir)
    state = str(status.get("state", "unknown"))

    proc = _PROCS.get(job_id)
    if proc is not None:
        returncode = proc.poll()
        if returncode is not None:
            status = _finalise(status, job_dir, returncode, None)
            state = str(status["state"])
    elif state == "running":
        pid = status.get("pid")
        if isinstance(pid, int) and not _pid_alive(pid):
            progress = parse_run_out_file(job_dir / RUN_OUT)
            status = _finalise(status, job_dir, None, progress.finished_code)
            state = str(status["state"])

    progress = parse_run_out_file(job_dir / RUN_OUT)
    result: dict[str, Any] = dict(status)
    result["state"] = state
    result["run_out_exists"] = (job_dir / RUN_OUT).is_file()
    result["current_time"] = progress.part_time
    result["tmax"] = progress.tmax
    result["percent"] = progress.percent
    result["last_part"] = progress.part
    result["total_steps"] = progress.total_steps
    result["steps_last_part"] = progress.steps_last_part
    result["wall_sec_per_sim_sec"] = progress.wall_sec_per_sim_sec
    result["wall_sec_per_sim_sec_note"] = (
        "solver 'Time/Sec' column: wall-clock seconds per simulated second (lower is faster)"
    )
    result["eta"] = progress.solver_eta
    result["eta_note"] = "solver's own projected finish time from the last PART row"
    result["particles"] = {
        "loaded": progress.loaded_particles,
        "total_case": progress.case_np,
        "fluid": progress.case_nfluid,
        "bound": progress.case_nbound,
        "current_part": progress.particles,
    }
    result["exception"] = progress.exception_text
    result["run_out_tail"] = _tail(job_dir / RUN_OUT)
    if state == "running":
        result["poll_hint"] = f'call job_status(job_id="{job_id}") again to track progress'
    return result
