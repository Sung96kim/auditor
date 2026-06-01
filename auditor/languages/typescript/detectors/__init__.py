"""Importing this package registers every built-in TypeScript/React detector."""

from auditor.languages.typescript.detectors import (  # noqa: F401
    a11y,
    complexity,
    design_system,
    dry,
    react,
    security,
    style,
    xfile,
)
