"""Importing this package registers every built-in TypeScript/React detector."""

from auditor.languages.typescript.detectors import (
    a11y,  # noqa: F401
    complexity,
    design_system,
    dry,
    react,
    security,
    style,
    xfile,
)
