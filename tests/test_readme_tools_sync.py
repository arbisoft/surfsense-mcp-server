"""Lock the README's tool list against the actually-registered tools.

The Tools section of the README is the canonical user-facing surface for
discovering what this server exposes. When a tool is renamed or added
without a README edit, MCP-client setup instructions silently rot and
support triage gets harder. A test is the cheapest place to catch that.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastmcp import Client

from surfsense_mcp.server import get_stdio_mcp

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"


def _readme_tools_section() -> str:
    """The body between ``## Tools`` and the next top-level header."""
    text = README.read_text(encoding="utf-8")
    start = text.index("## Tools")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


async def test_readme_lists_every_registered_tool() -> None:
    async with Client(get_stdio_mcp()) as client:
        registered = {tool.name for tool in await client.list_tools()}

    section = _readme_tools_section()
    documented = set(re.findall(r"`([a-z_]+)`", section))

    missing = registered - documented
    assert not missing, (
        f"README ## Tools section is missing {sorted(missing)} — "
        "rename/add a tool? Update README.md to keep client setup docs in sync."
    )
