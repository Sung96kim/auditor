"""Command-line interface (typer), split one file per command. ``apps`` holds the root ``app``
and the status console, ``options`` / ``helpers`` / ``summary`` the shared pieces, and each
command module owns its handler (and its sub-app, when it has one). This package ``__init__`` is
the composition root: importing the root commands registers them, and the sub-apps are mounted
here. The ``auditor.cli:app`` entry point resolves to the ``app`` exported below.
"""

from auditor.cli import (  # noqa: F401 — imported for their @app.command() side effects
    aggregate,
    crossfile,
    discover,
    manifest,
    report,
    scan,
    version,
)
from auditor.cli.apps import app
from auditor.cli.config import config_app
from auditor.cli.ignore import ignore_app
from auditor.cli.index import index_app
from auditor.cli.plugins import plugins_app
from auditor.cli.rules import rules_app
from auditor.cli.self_update import self_app
from auditor.languages.python.auditor import PythonAuditor

app.add_typer(index_app, name="index")
app.add_typer(ignore_app, name="ignore")
app.add_typer(config_app, name="config")
app.add_typer(rules_app, name="rules")
app.add_typer(plugins_app, name="plugins")
app.add_typer(self_app, name="self")

# ensure all built-in languages register for discovery's suffix list
_ = PythonAuditor

try:  # the graph commands need the optional [graph] extra (numpy + scikit-learn)
    from auditor.cli.graph import graph_app
except ImportError:
    from auditor.cli.graph_stub import graph_app

app.add_typer(graph_app, name="graph")

__all__ = ["app"]
