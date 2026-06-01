"""Importing this package registers every built-in TypeScript/React detector."""

from auditor.languages.typescript.detectors import (  # noqa: F401
    a11y,
    complexity,
    dry,
    react,
    security,
    style,
    xfile,
)
