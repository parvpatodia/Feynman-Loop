#!/usr/bin/env bash
# Build the one-click Claude Desktop bundle: dist/feynman-loop.mcpb
# Users install it by double-clicking (or Claude Desktop > Settings > Extensions), no terminal.
#
# WHY core deps only (no embeddings extra): pasted sources ground directly without the vector
# stack, which keeps the bundle small. Long-document grounding degrades gracefully and tells
# the user how to add the extra.
#
# NOTE: pip pulls platform wheels (pydantic-core etc.), so a bundle built here targets this
# platform. Build on each platform you want to ship, or in CI with a matrix.
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE=build/mcpb
rm -rf "$STAGE" dist
mkdir -p "$STAGE/server/lib" dist

cp mcpb/manifest.json "$STAGE/manifest.json"
cp mcpb/main.py "$STAGE/server/main.py"

python3 -m pip install --quiet --target "$STAGE/server/lib" --no-compile .

npx -y @anthropic-ai/mcpb pack "$STAGE" dist/feynman-loop.mcpb
echo "Built dist/feynman-loop.mcpb"
