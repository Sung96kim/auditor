"""Optional Code Mode pilot.

Code Mode (an experimental FastMCP transform) lets a client LLM discover the tools, write a
Python script that chains them in a sandbox, and receive only the final value — so large
intermediate payloads (a full scan feeding a filter, say) never flow through the model's
context. It is the strategic lever for auditing very large repos.

It stays OFF by default and is enabled explicitly, mirroring the ``[graph]`` extra's opt-in
guard: it requires both the ``code-mode`` extra (``pip install 'auditor[code-mode]'``, which
pulls ``fastmcp[code-mode]`` and its sandbox) AND the ``AUDITOR_CODE_MODE`` environment flag."""

from fastmcp import FastMCP

from auditor.config import GlobalPaths

try:
    from fastmcp.experimental.transforms.code_mode import CodeMode

    _CODE_MODE_OK = True
except ImportError:  # the [code-mode] extra (fastmcp sandbox deps) isn't installed
    _CODE_MODE_OK = False


def code_mode_available() -> bool:
    """Whether the Code Mode transform can be imported (the extra is installed)."""
    return _CODE_MODE_OK


def code_mode_requested() -> bool:
    """Whether the operator asked for Code Mode via ``$AUDITOR_CODE_MODE``."""
    return GlobalPaths().code_mode


def enable_code_mode(server: FastMCP) -> bool:
    """Add the Code Mode transform to ``server`` when both installed and requested. Returns
    whether it was actually enabled, so the caller can log the pilot state."""
    if not (_CODE_MODE_OK and code_mode_requested()):
        return False
    server.add_transform(CodeMode())
    return True
