#!/usr/bin/env bash
# SessionStart: make sure the Graphify tool is installed and the knowledge graph exists. The graph (graphify-out/, ~18 MB, graph.json
# rewritten whole on every build) is not committed: it is rebuilt here from the code in a few seconds, AST only, no LLM, nothing leaves
# the machine. Never fails a session: any problem just means no graph.
export PATH="$HOME/.local/bin:$PATH"
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
if ! command -v graphify >/dev/null 2>&1; then
  command -v uv >/dev/null 2>&1 || exit 0
  uv tool install graphifyy >/dev/null 2>&1 || exit 0
fi
command -v graphify >/dev/null 2>&1 || exit 0
if [ ! -f graphify-out/graph.json ]; then
  graphify update . >/dev/null 2>&1 || true
fi
exit 0
