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


def forge_home() -> Path:
    home = Path.home()
    for candidate in (home / ".forge", home / "forge"):
        if candidate.exists():
            return candidate
    return home / ".forge"  # forge's new default; created on first run


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


if __name__ == "__main__":
    main()
