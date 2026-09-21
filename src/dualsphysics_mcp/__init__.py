"""dualsphysics-mcp: DualSPHysics solver toolchain wrapped as MCP tools (stdio).

The package wraps the local DualSPHysics command-line tools (GenCase, the CPU
solver, PartVTK, MeasureTool) as Model Context Protocol tools and adds a pure
Python validation workflow for the classic 2D dam-break benchmark
(Koshizuka & Oka 1996). The solver itself is NOT distributed here: it is
invoked as a subprocess on the user's machine (DualSPHysics is LGPL-2.1+,
subprocess invocation carries no licence obligations for this MIT package).
"""

__version__ = "0.1.0"
