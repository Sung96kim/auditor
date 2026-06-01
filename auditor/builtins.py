"""Importing this module registers all built-in languages and detectors.

Centralizes the one bootstrap import so other modules can depend on registration via a
plain top-level import (no inline imports, no import cycle: ``PythonAuditor`` references
``ResolvedConfig`` only under TYPE_CHECKING).
"""

import auditor.languages.python.auditor  # noqa: F401  (registers PythonAuditor + every detector)
import auditor.reporters  # noqa: F401  (registers json/sarif/markdown reporters)
