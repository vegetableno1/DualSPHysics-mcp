"""Error codes for domain failures (surfaced as ``ToolError("<code>: <msg>")``)."""

from __future__ import annotations


class DualSphysError(Exception):
    """Base class for domain errors; carries a stable error code."""

    code = "DSPH_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")
        self.message = message


class ToolMissingError(DualSphysError):
    """A required DualSPHysics executable is not configured / not found."""

    code = "DSPH_TOOL_MISSING"


class RunFailedError(DualSphysError):
    """A wrapped tool ran but exited with a non-zero status."""

    code = "DSPH_RUN_FAILED"


class JobNotFoundError(DualSphysError):
    """Unknown job id (no jobs/<job_id>/status.json)."""

    code = "DSPH_JOB_NOT_FOUND"


class BadInputError(DualSphysError):
    """Invalid tool argument or unreadable input file."""

    code = "DSPH_BAD_INPUT"
