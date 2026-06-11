"""Bundle entry point. The .mcpb ships its dependencies in server/lib; this shim puts them on
the path and runs the stdio MCP server. FEYNMAN_HOME defaults to ~/.feynman-loop as everywhere."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "lib"))

from feynman_loop.mcp_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
