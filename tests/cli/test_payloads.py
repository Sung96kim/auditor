"""The `--json` key contract, pinned per command.

Every list was captured from the CLI before the payload models landed, so a retyped payload that
renames, drops or adds a key fails here rather than silently breaking an agent that parses it.
"""

import ast
import json
from collections.abc import Callable
from pathlib import Path
from types import UnionType
from typing import get_args, get_origin, get_type_hints

import pytest
from _support import cli_json, invoke
from pydantic import BaseModel, ValidationError

from auditor import cli
from auditor.cli import payloads as cli_payloads
from auditor.cli import render
from auditor.cli.helpers import present
from auditor.cli.payloads import (
    CrossfileReport,
    DetectorInfo,
    PluginsReport,
    SourceInfo,
)
from auditor.graph import flow
from auditor.graph import model as graph_model
from auditor.graph import payloads as graph_payloads
from auditor.graph.query import GraphQuery
from auditor.graph.refine import models as graph_refine_models


@pytest.fixture
def plain_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("class Foo:\n    def bar(self):\n        return 1\n")
    return tmp_path


OBJECT_KEYS: dict[str, list[str]] = {
    "crossfile": ["cross_file_findings"],
    "config check": ["policy_unknown", "root", "user_unknown"],
    "init": [
        "checked",
        "config",
        "home",
        "legacy_status",
        "migrated",
        "moved_from",
        "repo_dir",
        "schema",
        "unknown_keys",
        "written",
    ],
}
ROW_KEYS: dict[str, list[str]] = {
    "discover": ["file", "role"],
    "manifest": [
        "arg_count",
        "decorators",
        "field_count",
        "flags",
        "is_async",
        "kind",
        "line",
        "return_type",
        "symbol",
    ],
}


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("crossfile", ["crossfile"]),
        ("config check", ["config", "check", "-r"]),
        ("init", ["init", "--check", "-r"]),
    ],
)
def test_object_payload_keys_are_unchanged(plain_repo, name, argv):
    payload = cli_json(invoke(*argv, str(plain_repo), "--json"))
    assert sorted(payload) == OBJECT_KEYS[name]


def test_discover_row_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("discover", str(plain_repo), "--json"))
    assert payload and sorted(payload[0]) == ROW_KEYS["discover"]


def test_manifest_row_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("manifest", str(plain_repo / "a.py"), "--json"))
    assert payload and sorted(payload[0]) == ROW_KEYS["manifest"]


def test_config_show_json_is_the_settings_model(plain_repo):
    payload = cli_json(invoke("config", "show", "-r", str(plain_repo), "--json"))
    assert payload["extends"] == "base"
    assert "unknown_keys" not in payload  # the loader's field never reaches the wire


def test_present_serialises_a_model_not_a_dict(capsys):
    """The contract: `present` owns the dump, so no command hand-rolls `model_dump`."""
    present(CrossfileReport(cross_file_findings=3), lambda out, p: None, as_json=True)
    assert json.loads(capsys.readouterr().out) == {"cross_file_findings": 3}


def test_present_emits_an_empty_object_for_a_missing_payload(capsys):
    """`None` is the "nothing found" payload; `null` would change the wire contract."""
    present(None, lambda out, p: None, as_json=True)
    assert json.loads(capsys.readouterr().out) == {}


@pytest.fixture
def scanned_repo(plain_repo):
    """A repo with one indexed file and one persistent ignore, so the list commands are non-empty."""
    assert invoke("scan", str(plain_repo), "-i").exit_code == 0
    assert (
        invoke(
            "index", "add", str(plain_repo / "a.py"), "-r", str(plain_repo)
        ).exit_code
        == 0
    )
    assert (
        invoke(
            "ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo)
        ).exit_code
        == 0
    )
    return plain_repo


def test_index_add_keys_are_unchanged(plain_repo):
    payload = cli_json(
        invoke(
            "index", "add", str(plain_repo / "a.py"), "-r", str(plain_repo), "--json"
        )
    )
    assert sorted(payload) == ["added"]


def test_index_forget_keys_are_unchanged(scanned_repo):
    payload = cli_json(
        invoke("index", "forget", "-r", str(scanned_repo), "--yes", "--json")
    )
    assert sorted(payload) == ["removed", "repo"]


def test_ignore_clear_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("ignore", "clear", "-r", str(scanned_repo), "--json"))
    assert sorted(payload) == ["cleared"]


def test_index_list_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("index", "list", "-r", str(scanned_repo), "--json"))
    assert payload and sorted(payload[0]) == [
        "counts",
        "doc_path",
        "language",
        "last_scanned",
        "lines",
        "path",
        "role",
        "sha256",
    ]


def test_index_repos_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("index", "repos", "--json"))
    assert payload and sorted(payload[0]) == ["last_scanned", "name", "repo"]


def test_ignore_list_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("ignore", "list", "-r", str(scanned_repo), "--json"))
    assert payload and sorted(payload[0]) == [
        "created_at",
        "evidence_hash",
        "file",
        "id",
        "line",
        "reason",
        "rule_id",
    ]


def test_ignore_add_keys_are_unchanged(plain_repo):
    payload = cli_json(
        invoke(
            "ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo), "--json"
        )
    )
    assert sorted(payload) == ["file", "id", "line", "note", "reason", "rule_id"]


def test_ignore_rm_keys_are_unchanged(plain_repo):
    invoke("ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo))
    payload = cli_json(invoke("ignore", "rm", "1", "-r", str(plain_repo), "--json"))
    assert sorted(payload) == ["removed", "selector"]


def test_rules_list_row_keys_are_unchanged():
    payload = cli_json(invoke("rules", "list", "--json"))
    assert sorted(payload[0]) == [
        "category",
        "default_severity",
        "framework",
        "rule_id",
        "source",
        "standard_refs",
        "verdict_kind",
    ]


def test_plugins_list_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("plugins", "list", "-r", str(plain_repo), "--json"))
    assert sorted(payload) == ["detectors", "languages", "reporters", "warnings"]


def test_plugins_list_detector_entry_keys_are_unchanged(plain_repo):
    """The per-entry shape is the half `test_plugins_list_keys_are_unchanged` cannot see, and it
    is the half the bug batch may widen."""
    payload = cli_json(invoke("plugins", "list", "-r", str(plain_repo), "--json"))
    first = payload["detectors"][next(iter(payload["detectors"]))]
    assert sorted(first) == ["category", "framework", "source"]


@pytest.mark.parametrize(
    ("model", "raw", "unknown"),
    [
        (DetectorInfo, {"category": "security", "source": "built-in"}, "hits"),
        (SourceInfo, {"source": "built-in"}, "hits"),
        (
            PluginsReport,
            {"detectors": {}, "languages": {}, "reporters": {}},
            "formatters",
        ),
    ],
)
def test_a_registry_key_no_model_declares_fails_loudly(model, raw, unknown):
    """`extra="forbid"` is the whole guard: `REGISTRY.snapshot()` is untyped, so a section or an
    entry field it gains has to raise here rather than be dropped on the way to the wire."""
    assert model.model_validate(raw)  # the declared shape still validates
    with pytest.raises(ValidationError, match=unknown):
        model.model_validate({**raw, unknown: {}})


def _annotated(fn: Callable[..., object], param: str) -> frozenset[str]:
    """Every model name one annotation admits, with the ``| None`` arm dropped."""
    hint = get_type_hints(fn)[param]
    parts = get_args(hint) if get_origin(hint) is UnionType else (hint,)
    return frozenset(part.__name__ for part in parts if part is not type(None))


RENDERERS = [name for name in dir(render) if name.startswith("render_")]


@pytest.mark.parametrize("name", RENDERERS)
def test_every_renderer_names_the_payload_it_reads(name):
    """`present` is generic over that annotation, so a renderer typed to `Any` or to bare
    `BaseModel` is the one place a mispairing could still slip through."""
    admitted = _annotated(getattr(render, name), "payload")
    assert admitted and not admitted & {"Any", "BaseModel"}


QUERY_RENDERERS = {
    "related": "render_graph_related",
    "neighbors": "render_graph_neighbors",
    "concept": "render_graph_concept",
    "clusters": "render_graph_clusters",
    "search": "render_graph_search",
    "usages": "render_graph_usages",
    "flow": "render_graph_flow",
}


@pytest.mark.parametrize("method", sorted(QUERY_RENDERERS))
def test_each_graph_query_payload_matches_its_renderer(method):
    """The eight graph payloads used to be erased by a stringly-keyed `getattr` one line after
    the query produced them."""
    assert _annotated(getattr(GraphQuery, method), "return") == _annotated(
        getattr(render, QUERY_RENDERERS[method]), "payload"
    )


# every payload model a command can name at a `present` call site
MODEL_NAMES = frozenset(
    name
    for module in (cli_payloads, graph_payloads, flow)
    for name, obj in vars(module).items()
    if isinstance(obj, type) and issubclass(obj, BaseModel)
)


def _constructed(node: ast.expr) -> str | None:
    """The payload model a call constructs, or ``None`` when the argument is built elsewhere."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        name = node.func.value.id  # `Model.of(...)` / `Model.model_validate(...)`
    else:
        return None
    return name if name in MODEL_NAMES else None


def _payload_source(node: ast.expr) -> frozenset[str]:
    """The models a `present` argument can produce: one built at the call site, or the return of
    the ``GraphQuery`` method it awaits. Empty when the argument is neither."""
    if (built := _constructed(node)) is not None:
        return frozenset({built})
    for inner in ast.walk(node):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in QUERY_RENDERERS
        ):
            return _annotated(getattr(GraphQuery, inner.func.attr), "return")
    return frozenset()


def _rendered_by(node: ast.expr) -> str | None:
    """The renderer a `present` call passes, unwrapping the one `partial(...)` binding."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and node.args:
        return _rendered_by(node.args[0])
    return None


def _present_pairs() -> list[tuple[str, frozenset[str], str]]:
    """(file, payload models, renderer) for each `present(...)` whose payload can be read."""
    out: list[tuple[str, frozenset[str], str]] = []
    for path in sorted(Path(cli.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "present"
                and len(node.args) == 2
            ):
                continue
            payload, renderer = (
                _payload_source(node.args[0]),
                _rendered_by(node.args[1]),
            )
            if payload and renderer is not None:
                out.append((path.name, payload, renderer))
    return out


@pytest.mark.parametrize(
    ("module", "payload", "renderer"),
    _present_pairs(),
    ids=lambda value: (
        "|".join(sorted(value)) if isinstance(value, frozenset) else value
    ),
)
def test_every_present_call_pairs_its_payload_with_its_renderer(
    module, payload, renderer
):
    """No type checker runs here, so the pairing `present`'s TypeVar expresses is asserted."""
    assert payload <= _annotated(getattr(render, renderer), "payload"), module


def test_the_pairing_sweep_finds_every_command_whose_payload_can_be_read():
    """A sweep that matched nothing would make the case above vacuous. The commands missing here
    build their payload in a helper the call site cannot name.

    `graph_refine.py` must stay out: the moment one of its `present(...)` call sites names a
    payload model, its payloads join this sweep and every renderer pairing here has to know about
    the refine half, which no fast CLI command may import."""
    assert {module for module, _, _ in _present_pairs()} == {
        "config.py",
        "crossfile.py",
        "discover.py",
        "graph.py",
        "ignore.py",
        "index.py",
        "init.py",
        "manifest.py",
        "plugins.py",
        "rules.py",
    }


def test_an_assessment_on_the_wire_keeps_the_tuple_lengths_and_drops_their_contents():
    """A fifty row page carrying every changed node id would fight the log's own row cap (P8)."""
    assessment = graph_refine_models.Assessment(
        files=("m.py",),
        added_nodes=("m.py::a", "m.py::b"),
        facts_changed_nodes=("m.py::c",),
        new_pairs=(graph_refine_models.NodePair(node_id="m.py::c", name="widen"),),
        stale_refinements=(1, 2, 3),
        deferred_pairs=4,
        verdict=graph_refine_models.Decision(
            decision=graph_refine_models.AssessmentDecision.SKIP,
            reason="no new questions",
        ),
    )
    wire = graph_payloads.AssessmentPayload.of(assessment)
    assert (wire.added_nodes, wire.facts_changed_nodes) == (2, 1)
    assert (wire.new_pairs, wire.stale_refinements, wire.deferred_pairs) == (1, 3, 4)
    assert wire.verdict.reason == "no new questions"
    assert "m.py::a" not in json.dumps(wire.model_dump(mode="json"))


def test_a_trigger_detail_on_the_wire_caps_its_paths_and_still_counts_them_all():
    paths = tuple(f"f{i}.py" for i in range(graph_model.LOG_FILE_CAP + 5))
    wire = graph_payloads.TriggerDetailPayload.of(
        graph_refine_models.TriggerDetail(files=paths)
    )
    assert len(wire.files) == graph_model.LOG_FILE_CAP
    assert wire.file_count == len(paths)
    assert wire.assessment is None
