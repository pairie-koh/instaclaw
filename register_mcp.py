"""Register instaclaw's kuri MCP server in forge's user-level config so the
codegraff agent can drive the browser. Idempotent — safe to run on every setup.

forge applies an interactive trust gate to *project-local* .mcp.json files and
rejects them headlessly (justrach/codegraff#152), so the server must live in the
user-level config (~/forge/.mcp.json or ~/.forge/.mcp.json), which is trusted.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
# Prefer the codegraff venv interpreter; fall back to whatever runs this script.
_VENV_PY = ROOT / ".venv-cg" / "bin" / "python"
PY = str(_VENV_PY if _VENV_PY.exists() else Path(sys.executable))
KURI_MCP = str(ROOT / "kuri_mcp.py")

AGENT_ID = "kuri-scraper"
AGENT_SRC = ROOT / ".forge" / "agents" / f"{AGENT_ID}.md"


def forge_home() -> Path:
    home = Path.home()
    for candidate in (home / ".forge", home / "forge"):
        if candidate.exists():
            return candidate
    return home / ".forge"  # forge's new default; created on first run


def agents_dir() -> Path:
    """forge's global custom-agents dir (base_path/agents). Loaded regardless
    of cwd, and — unlike a project-local .forge/ — not subject to the headless
    trust gate that rejects a project-local .mcp.json (justrach/codegraff#152)."""
    return forge_home() / "agents"


def ensure_kuri_agent():
    """Install the kuri-scoped scrape agent (.forge/agents/kuri-scraper.md) into
    forge's global agents dir so the SDK's agent="kuri-scraper" selection
    resolves to a tool-scoped (kuri-only) forge agent. Idempotent: rewrites only
    when the installed copy is missing or stale. Returns the dest path, or None
    if the source file is absent."""
    if not AGENT_SRC.exists():
        return None
    dst_dir = agents_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{AGENT_ID}.md"
    src_text = AGENT_SRC.read_text(encoding="utf-8")
    if not dst.exists() or dst.read_text(encoding="utf-8") != src_text:
        dst.write_text(src_text, encoding="utf-8")
    return dst


def main() -> None:
    home = forge_home()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / ".mcp.json"

    cfg = {"mcpServers": {}}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:  # noqa
            cfg = {"mcpServers": {}}
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"]["kuri"] = {"command": PY, "args": [KURI_MCP], "disable": False}
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"registered 'kuri' MCP server in {cfg_path}")
    print(f"  command: {PY} {KURI_MCP}")

    agent_path = ensure_kuri_agent()
    if agent_path:
        print(f"installed '{AGENT_ID}' agent at {agent_path}")
    else:
        print(f"skipped agent install: {AGENT_SRC} not found")


if __name__ == "__main__":
    main()
