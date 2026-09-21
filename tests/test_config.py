"""Tests for env-var configuration and tool discovery."""

from __future__ import annotations

import os
from pathlib import Path

from dualsphysics_mcp import config
from tests.conftest import write_fake_binary


def test_env_var_wins(fake_toolchain: Path, clean_env: dict[str, str]) -> None:
    custom = fake_toolchain.parent / "elsewhere" / "GenCase_linux64"
    write_fake_binary(custom)
    resolved = config.resolve_tool(
        "gencase", env={"DSPH_GENCASE": str(custom), **clean_env}, scan_roots=[]
    )
    assert resolved.path == custom
    assert resolved.source == "env"
    assert resolved.found


def test_env_var_pointing_nowhere_is_reported_missing(clean_env: dict[str, str]) -> None:
    resolved = config.resolve_tool(
        "solver", env={"DSPH_SOLVER": "/nope/DualSPHysicsCPU", **clean_env}, scan_roots=[]
    )
    assert resolved.source == "env"
    assert not resolved.found
    assert "Makefile_cpu" in resolved.install_hint  # solver-specific build hint


def test_scan_discovery(fake_toolchain: Path, clean_env: dict[str, str]) -> None:
    # scan_roots are candidate *bin/linux* directories (they replace defaults).
    resolved = config.resolve_all(env=clean_env, scan_roots=[fake_toolchain])
    for key in config.TOOL_KEYS:
        assert resolved[key].found, key
        assert resolved[key].source == "scan"
        path = resolved[key].path
        assert path is not None and path.is_file()


def test_missing_when_nothing_configured(clean_env: dict[str, str]) -> None:
    resolved = config.resolve_tool("gencase", env=clean_env, scan_roots=[])
    assert not resolved.found
    assert resolved.source == "missing"
    assert resolved.path is None
    assert "DSPH_GENCASE" in resolved.install_hint


def test_solver_glob_prefers_highest_version(
    fake_toolchain: Path, clean_env: dict[str, str]
) -> None:
    write_fake_binary(fake_toolchain / "DualSPHysics5.0CPU_linux64")
    resolved = config.resolve_tool("solver", env=clean_env, scan_roots=[fake_toolchain])
    assert resolved.path is not None
    assert resolved.path.name == "DualSPHysics5.4CPU_linux64"


def test_jobs_dir_default_and_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert config.jobs_dir(env={}) == tmp_path / "jobs"
    custom = tmp_path / "elsewhere"
    assert config.jobs_dir(env={"DSPH_JOBS_DIR": str(custom)}) == custom


def test_omp_threads_env_and_default(monkeypatch) -> None:
    assert config.omp_threads(env={"DSPH_OMP_THREADS": "3"}) == 3
    assert config.omp_threads(env={"DSPH_OMP_THREADS": "bogus"}) == (os.cpu_count() or 1)
    assert config.omp_threads(env={}) >= 1


def test_solver_runtime_env_prepends_library_dir(tmp_path: Path) -> None:
    solver = tmp_path / "bin" / "DualSPHysics5.4CPU_linux64"
    env = config.solver_runtime_env(solver, base_env={"LD_LIBRARY_PATH": "/opt/other"})
    parts = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert parts[0] == str(solver.parent)
    assert "/opt/other" in parts
    assert len(parts) == len(set(parts))  # de-duplicated


def test_cached_tool_path_uses_environment(monkeypatch, fake_toolchain: Path) -> None:
    config.clear_cache()
    try:
        monkeypatch.setenv("DSPH_GENCASE", str(fake_toolchain / "GenCase_linux64"))
        assert config.tool_path("gencase") == fake_toolchain / "GenCase_linux64"
    finally:
        config.clear_cache()
