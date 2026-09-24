"""End-to-end over stdio: spawn the server, drive all seven tools.

No solver needed: the environment is forced "no toolchain" for the
deterministic assertions, and the local toolchain (when present) is probed
in a separate marked test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.conftest import LOCAL_TOOLCHAIN, REPO_ROOT

EXPECTED_TOOLS = {
    "check_environment",
    "gencase",
    "run_case",
    "job_status",
    "partvtk",
    "measure_tool",
    "validate_dambreak",
}

DSPH_ENV_KEYS = (
    "DSPH_GENCASE",
    "DSPH_SOLVER",
    "DSPH_PARTVTK",
    "DSPH_MEASURETOOL",
    "DSPH_JOBS_DIR",
    "DSPH_OMP_THREADS",
)


def server_params(env_extra: dict[str, str], home: Path) -> StdioServerParameters:
    """Subprocess env with tool discovery isolated (bare PATH, throwaway HOME).

    HOME is redirected so the scan of conventional install roots (~/**/DualSPHysics*)
    finds nothing: the "missing toolchain" assertions stay deterministic even on
    a machine that HAS DualSPHysics installed.
    """
    env = {**os.environ, "PATH": "/usr/bin:/bin", "HOME": str(home)}
    for key in DSPH_ENV_KEYS:
        env.pop(key, None)
    env.update(env_extra)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "dualsphysics_mcp"],
        env=env,
        cwd=str(REPO_ROOT),
    )


async def test_stdio_server_registers_all_seven_tools(tmp_path: Path) -> None:
    async with (
        stdio_client(server_params({}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names >= EXPECTED_TOOLS


async def test_check_environment_reports_missing_with_hints(tmp_path: Path) -> None:
    async with (
        stdio_client(server_params({}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("check_environment")
        assert not result.is_error
        payload = result.structured_content
        assert payload is not None
        assert [t["name"] for t in payload["tools"]] == [
            "gencase",
            "solver",
            "partvtk",
            "measuretool",
        ]
        assert payload["ready"] is False
        assert set(payload["missing"]) == {"gencase", "solver", "partvtk", "measuretool"}
        for tool in payload["tools"]:
            assert tool["found"] is False
            assert "DSPH_" in tool["install_hint"]


async def test_validate_dambreak_over_stdio(tmp_path: Path) -> None:
    csv = tmp_path / "measure.csv"
    csv.write_text(
        "time,rhop0,rhop1\n0.100,1000.0,0.0\n0.300,1000.0,1000.0\n",
        encoding="utf-8",
    )
    jobs = str(tmp_path / "jobs")
    async with (
        stdio_client(server_params({"DSPH_JOBS_DIR": jobs}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "validate_dambreak",
            {
                "csv_path": str(csv),
                "points": [[1.0, 0.0, 0.03], [2.0, 0.0, 0.03]],
            },
        )
        assert not result.is_error
        payload = result.structured_content
        assert payload is not None
        assert payload["n_samples"] == 2
        assert payload["mae_m"] >= 0
        assert payload["experiment"] == "koshizuka1996"


async def test_gencase_without_toolchain_is_a_clean_tool_error(tmp_path: Path) -> None:
    # A valid (existing) case XML so the input check passes and the tool
    # lookup is what fails: proves validation order without a toolchain.
    case_xml = tmp_path / "whatever_Def.xml"
    case_xml.write_text("<case/>", encoding="utf-8")
    async with (
        stdio_client(server_params({"DSPH_JOBS_DIR": str(tmp_path)}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("gencase", {"xml_path": str(case_xml)})
        assert result.is_error
        text = "".join(block.text for block in result.content if block.type == "text")
        assert "DSPH_TOOL_MISSING" in text


async def test_run_case_rejects_unknown_case_cleanly(tmp_path: Path) -> None:
    async with (
        stdio_client(server_params({"DSPH_JOBS_DIR": str(tmp_path)}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "run_case", {"case_path": str(tmp_path / "nope" / "CaseX")}
        )
        # Input validation precedes tool lookup: unknown cases are DSPH_BAD_INPUT
        # whether or not a solver is installed.
        assert result.is_error
        text = "".join(block.text for block in result.content if block.type == "text")
        assert "DSPH_BAD_INPUT" in text


async def test_job_status_unknown_id_is_clean_error(tmp_path: Path) -> None:
    async with (
        stdio_client(server_params({"DSPH_JOBS_DIR": str(tmp_path)}, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("job_status", {"job_id": "job-does-not-exist"})
        assert result.is_error
        text = "".join(block.text for block in result.content if block.type == "text")
        assert "DSPH_JOB_NOT_FOUND" in text


@pytest.mark.solver
async def test_check_environment_with_local_toolchain(tmp_path: Path) -> None:
    """Probe the real local toolchain (auto-skipped when not installed)."""
    bin_dir = LOCAL_TOOLCHAIN
    env = {
        "DSPH_GENCASE": str(bin_dir / "GenCase_linux64"),
        "DSPH_SOLVER": str(next(iter(sorted(bin_dir.glob("DualSPHysics*CPU_linux64"))))),
        "DSPH_PARTVTK": str(bin_dir / "PartVTK_linux64"),
        "DSPH_MEASURETOOL": str(bin_dir / "MeasureTool_linux64"),
        "DSPH_JOBS_DIR": str(tmp_path),
    }
    async with (
        stdio_client(server_params(env, tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("check_environment")
        assert not result.is_error
        payload = result.structured_content
        assert payload is not None
        assert payload["ready"] is True
        gencase_entry = next(t for t in payload["tools"] if t["name"] == "gencase")
        assert gencase_entry["version"] is not None
        solver_entry = next(t for t in payload["tools"] if t["name"] == "solver")
        assert solver_entry["features"]["CPU"] is True
        # Local build note: -DDISABLE_WAVEGEN must be surfaced when present.
        if solver_entry["features"].get("WaveGen") is False:
            assert solver_entry["notes"] is not None
