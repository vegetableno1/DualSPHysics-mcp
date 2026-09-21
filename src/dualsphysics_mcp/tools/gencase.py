"""gencase: wrap GenCase (case XML -> Case.xml + Case.bi4 + preview VTK).

GenCase CLI contract (doc/help/GenCase_Help.out)::

    GenCase config_in config_out [options]

``config_in`` / ``config_out`` are path bases WITHOUT the ``.xml`` suffix
(official scripts call ``GenCase CaseDambreakVal2D_Def OUT/CaseDambreakVal2D
-save:all``). This module accepts paths with or without the suffix and strips
it. Default output name drops the ``_Def`` suffix; default output directory
follows the official ``<name>_out`` convention.

Particle counts: GenCase's console text varies across versions, so counts are
taken primarily from the VTK files it writes with ``-save:all`` (the ASCII
``POINTS <n>`` header — present even in binary VTK payloads), with tolerant
console regexes as fallback.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import config
from ..errors import BadInputError, RunFailedError, ToolMissingError

GENCASE_TIMEOUT_S = 600.0


def strip_xml_suffix(path: Path) -> Path:
    """``CaseX.xml`` -> ``CaseX``; dotted names keep everything before ``.xml``.

    Unlike :meth:`Path.with_suffix("")` this never rewrites a stem that itself
    contains dots (``Run2026.1.xml`` -> ``Run2026.1``, not ``Run2026``).
    """
    name = path.name
    if name.endswith(".xml"):
        return path.parent / name[: -len(".xml")]
    return path


def append_suffix(path: Path, suffix: str) -> Path:
    """``CaseX`` -> ``CaseX<suffix>`` by concatenation (dotted stems survive)."""
    return path.parent / (path.name + suffix)


_VTK_POINTS = re.compile(rb"POINTS\s+(\d+)")
_COUNT_PATTERNS = {
    "total": [
        # GenCase v5.4: "Total particles: 21,001 (bound=1001 ... fluid=20000)"
        re.compile(r"(?i)total\s+particles\s*[=:]\s*([0-9][0-9,\.]*)"),
        re.compile(r"(?i)points\s+loaded\s*[=:]\s*([0-9][0-9,\.]*)"),
        re.compile(r"(?i)total\s+(?:number\s+of\s+)?particles\s*[=:]\s*([0-9][0-9,\.]*)"),
        re.compile(r"(\d+)\s+particles\s+successfully\s+stored"),
    ],
    "fluid": [
        # "... fluid=20000)" tail of the Total particles line + block summary
        re.compile(r"(?i)fluid\s*=\s*([0-9][0-9,\.]*)\)"),
        re.compile(r"(?im)^\s*Fluid[.\s]*:\s*([0-9][0-9,\.]*)"),
        re.compile(r"(?i)fluid\s+particles\s*[=:]\s*([0-9][0-9,\.]*)"),
    ],
    "bound": [
        re.compile(r"\(bound\s*=\s*([0-9][0-9,\.]*)", re.IGNORECASE),
        re.compile(r"(?im)^\s*Fixed[.\s]*:\s*([0-9][0-9,\.]*)"),
        re.compile(r"(?i)(?:bound|boundary)\s+particles\s*[=:]\s*([0-9][0-9,\.]*)"),
    ],
}


def case_base_name(xml_path: Path) -> str:
    """``CaseX_Def.xml`` / ``CaseX_Def`` / ``CaseX.xml`` -> ``CaseX``."""
    stem = strip_xml_suffix(xml_path).name
    return stem[:-4] if stem.lower().endswith("_def") else stem


def build_gencase_command(
    gencase_exe: Path,
    xml_path: Path,
    out_dir: Path,
    out_name: str | None = None,
    save_modes: str = "all",
    extra_args: list[str] | None = None,
) -> list[str]:
    """Argv for GenCase: ``<exe> <in_base> <out_base> [-save:<modes>] [...]``."""
    in_base = str(strip_xml_suffix(xml_path))
    name = out_name or case_base_name(xml_path)
    argv = [str(gencase_exe), in_base, str(out_dir / name)]
    if save_modes:
        argv.append(f"-save:{save_modes}")
    if extra_args:
        argv.extend(extra_args)
    return argv


def count_vtk_points(vtk_file: Path) -> int | None:
    """Particle count from a VTK header (works for ASCII and binary payloads)."""
    try:
        with vtk_file.open("rb") as handle:
            head = handle.read(65536)
    except OSError:
        return None
    match = _VTK_POINTS.search(head)
    return int(match.group(1)) if match else None


def parse_gencase_output(stdout: str) -> dict[str, int | None]:
    """Best-effort particle counts from GenCase console text (last match wins)."""
    counts: dict[str, int | None] = {"total": None, "fluid": None, "bound": None}
    for label, patterns in _COUNT_PATTERNS.items():
        for pattern in patterns:
            matches = pattern.findall(stdout)
            if matches:
                digits = re.sub(r"[^0-9]", "", matches[-1])
                if digits:
                    counts[label] = int(digits)
    return counts


def _classify_vtk(out_base: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for vtk in sorted(out_base.parent.glob(out_base.name + "*.vtk")):
        lower = vtk.name.lower()
        if "fluid" in lower:
            label = "fluid"
        elif "bound" in lower:
            label = "bound"
        elif "all" in lower:
            label = "all"
        else:
            label = "other"
        files[label] = {"path": str(vtk), "points": count_vtk_points(vtk)}
    return files


def run_gencase(
    xml_path: str,
    output_dir: str | None = None,
    out_name: str | None = None,
    save_modes: str = "all",
    extra_args: list[str] | None = None,
    timeout: float = GENCASE_TIMEOUT_S,
) -> dict[str, Any]:
    """Run GenCase synchronously (it takes seconds) and summarise its outputs."""
    exe = config.tool_path("gencase")
    if exe is None or not exe.is_file():
        raise ToolMissingError(
            "GenCase executable not found; set DSPH_GENCASE (see check_environment)"
        )

    xml = Path(xml_path).expanduser().resolve()
    xml_actual = xml if xml.name.endswith(".xml") else append_suffix(xml, ".xml")
    if not xml_actual.is_file():
        raise BadInputError(f"case XML not found: {xml_actual}")

    name = out_name or case_base_name(xml)
    out_dir = Path(output_dir).expanduser() if output_dir else xml.parent / f"{name}_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_base = out_dir / name

    argv = build_gencase_command(exe, xml_actual, out_dir, out_name, save_modes, extra_args)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(out_dir.parent),
            env=config.solver_runtime_env(exe),
        )
    except subprocess.TimeoutExpired as exc:
        raise RunFailedError(f"GenCase timed out after {timeout:.0f}s") from exc
    duration = round(time.monotonic() - started, 3)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise RunFailedError(
            f"GenCase exited with code {proc.returncode}; stderr tail: {stderr[-800:]!r}"
        )

    out_xml = append_suffix(out_base, ".xml")
    out_bi4 = append_suffix(out_base, ".bi4")
    vtk_files = _classify_vtk(out_base)
    counts = parse_gencase_output(stdout)
    vtk_points: dict[str, int | None] = {
        label: entry["points"] for label, entry in vtk_files.items()
    }
    if vtk_points.get("all") is not None:
        counts["total"] = vtk_points["all"]
    if vtk_points.get("fluid") is not None:
        counts["fluid"] = vtk_points["fluid"]
    if vtk_points.get("bound") is not None:
        counts["bound"] = vtk_points["bound"]

    return {
        "returncode": proc.returncode,
        "command": argv,
        "out_xml": str(out_xml),
        "out_bi4": str(out_bi4),
        "bi4_exists": out_bi4.is_file(),
        "vtk_files": vtk_files,
        "particle_counts": counts,
        "stdout_tail": stdout[-1500:],
        "stderr_tail": stderr[-800:],
        "duration_s": duration,
        "next_step": (f'run_case(case_path="{strip_xml_suffix(out_xml)}") to start the solver'),
    }
