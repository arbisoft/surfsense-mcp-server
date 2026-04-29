"""Tests for ``surfsense_mcp.__main__`` import-time hygiene.

The lib's ``moneta_mcp_auth.tests`` cover ``configure_json_logging`` /
``resolve_http_port`` / ``resolve_log_level`` directly. What surfsense
specifically owns is that *its* entry-point module stays import-clean —
i.e. importing :mod:`surfsense_mcp.__main__` must not mutate global
logging handlers (otherwise pytest's own log capture and any in-process
consumer that imports the package would lose their handlers).
"""

from __future__ import annotations

import importlib
import logging
import sys


def test_importing_main_does_not_mutate_fastmcp_logger() -> None:
    fastmcp_logger = logging.getLogger("fastmcp")
    sentinel = logging.NullHandler()
    fastmcp_logger.addHandler(sentinel)
    original_propagate = fastmcp_logger.propagate
    fastmcp_logger.propagate = True
    try:
        sys.modules.pop("surfsense_mcp.__main__", None)
        importlib.import_module("surfsense_mcp.__main__")

        assert sentinel in fastmcp_logger.handlers, "module import stripped existing handlers"
        assert fastmcp_logger.propagate is True, "module import flipped propagate to False"
    finally:
        fastmcp_logger.removeHandler(sentinel)
        fastmcp_logger.propagate = original_propagate
