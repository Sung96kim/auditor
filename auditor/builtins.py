"""Importing this module registers all built-in languages and detectors.

Centralizes the one bootstrap import so other modules can depend on registration via a
plain top-level import (no inline imports, no import cycle: ``PythonAuditor`` references
``ResolvedConfig`` only under TYPE_CHECKING).
"""

import auditor.languages.bash.auditor  # noqa: F401  (registers BashAuditor + shell detectors)
import auditor.languages.python.auditor  # noqa: F401  (registers PythonAuditor + every detector)
import auditor.reporters  # noqa: F401  (registers json/sarif/markdown reporters)

# TypeScript support needs the optional `ts` extra (tree-sitter). Register it when present;
# without the extra the core Python auditor still works.
try:
    import auditor.languages.typescript.auditor  # noqa: F401  (registers TypeScriptAuditor + TS detectors)

    TYPESCRIPT_AVAILABLE = True
except ImportError:
    TYPESCRIPT_AVAILABLE = False
