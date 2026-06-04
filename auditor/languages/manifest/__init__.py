"""Manifest auditing — supply-chain checks over dependency/build manifests, dispatched by
*filename* rather than suffix (``package.json`` today; ``requirements.txt``/``pyproject.toml``
next). A thin parse layer turns each manifest into structured data the detectors read."""
