"""Detectors in security/deserialize.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/deserialize"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# yaml.load — SafeLoader positional arg suppresses the finding
# ---------------------------------------------------------------------------


def test_yaml_load_safe_loader_positional_does_not_fire():
    # SafeLoader passed as the second positional arg → safe, must not flag
    src = "import yaml\nyaml.load(stream, yaml.SafeLoader)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src))


def test_yaml_load_base_loader_fires():
    # BaseLoader is not a "Safe*" loader → must flag
    src = "import yaml\nyaml.load(stream, yaml.BaseLoader)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# joblib.load — pickle under the hood (sklearn/ML model files) → arbitrary code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "import joblib\nm = joblib.load(path)\n",
        "import joblib as jl\nm = jl.load(BytesIO(blob))\n",  # aliased import resolves
    ],
)
def test_joblib_load_fires(src):
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src))


def test_joblib_dump_clean():
    # dump writes (serializes), not a deserialization sink
    src = "import joblib\njoblib.dump(obj, path)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# PY-SEC-UNSAFE-DESERIALIZE — complex edge-case parametrized tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "import joblib\nm = joblib.load(p)\n",
        "import joblib as jl\nm = jl.load(p)\n",
        "import pickle\npickle.loads(b)\n",
        "import dill\ndill.load(f)\n",
        "import cloudpickle\ncloudpickle.load(f)\n",
        "import yaml\nyaml.load(s)\n",
    ],
    ids=[
        "joblib-load",
        "joblib-aliased",
        "pickle-loads",
        "dill-load",
        "cloudpickle-load",
        "yaml-load-no-loader",
    ],
)
def test_unsafe_deserialize_flagged_variants(src: str) -> None:
    # All of these call an unsafe deserialization function; each must fire.
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src)), (
        f"expected PY-SEC-UNSAFE-DESERIALIZE for:\n{src}"
    )


@pytest.mark.parametrize(
    "src",
    [
        "import joblib\njoblib.dump(o, p)\n",
        "import yaml\nyaml.safe_load(s)\n",
        "import yaml\nyaml.load(s, Loader=yaml.SafeLoader)\n",
        "import json\njson.loads(s)\n",
    ],
    ids=[
        "joblib-dump",
        "yaml-safe-load",
        "yaml-load-safe-loader-kwarg",
        "json-loads",
    ],
)
def test_unsafe_deserialize_clean_variants(src: str) -> None:
    # None of these are unsafe deserialization sinks; none must fire.
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src)), (
        f"PY-SEC-UNSAFE-DESERIALIZE must NOT fire for:\n{src}"
    )


# ---------------------------------------------------------------------------
# Obscure edge-case tests — discovered+pinned (run to characterize, then asserted)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "from pickle import loads\nloads(b)\n",
        "from pickle import loads as l\nl(b)\n",  # aliased from-import
        "from joblib import load\nload(p)\n",
        "from yaml import unsafe_load\nunsafe_load(s)\n",
    ],
    ids=["pickle-loads", "pickle-loads-aliased", "joblib-load", "yaml-unsafe-load"],
)
def test_from_import_sink_flagged(src: str) -> None:
    # `name_origin_map` resolves a from-import bound name back to its origin
    # (`loads` -> `pickle.loads`), so from-import deserialization sinks are now flagged.
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src)), (
        f"from-import sink should resolve to its origin and flag:\n{src}"
    )


def test_from_import_safe_name_not_flagged() -> None:
    # a same-named function from an unrelated module must NOT resolve to a known sink
    src = "from mymod import loads\nloads(b)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src)), (
        "from-import of an unrelated 'loads' must not be confused with pickle.loads"
    )


def test_aliased_pickle_module_loads_flagged() -> None:
    # CORRECT: `import pickle as p; p.loads(b)` — import_alias_map maps `p` -> `pickle`,
    # so resolve_dotted("p.loads", {"p": "pickle"}) == "pickle.loads" → in _UNSAFE_LOADERS → FLAGGED.
    src = "import pickle as p\np.loads(b)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src)), (
        "aliased-module pickle.loads must be flagged via import-alias resolution"
    )


def test_yaml_load_variable_loader_no_safe_in_name_flagged() -> None:
    # CORRECT: `yaml.load(s, Loader=Loader)` where `Loader` is a plain variable — dotted_name
    # of the Loader= value is just "Loader"; "Safe" is NOT in "Loader" → _is_safe_yaml returns
    # False → rule flags it as unsafe.
    src = "import yaml\nLoader = something\nyaml.load(s, Loader=Loader)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src)), (
        "yaml.load with a non-Safe variable Loader must be flagged"
    )


def test_pandas_read_pickle_not_flagged() -> None:
    # FN-GAP (known scope): pandas.read_pickle is not in _UNSAFE_LOADERS — only the core
    # pickle-family and joblib/dill/cloudpickle/yaml sinks are enumerated.
    src = "import pandas\npandas.read_pickle(p)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src)), (
        "pandas.read_pickle is outside the rule's loader set — known scope gap, NOT flagged"
    )


def test_numpy_load_allow_pickle_not_flagged() -> None:
    # FN-GAP (known scope): numpy.load(allow_pickle=True) is not covered — the rule only
    # checks the callee name against _UNSAFE_LOADERS; numpy.load is not in that set.
    src = "import numpy\nnumpy.load(p, allow_pickle=True)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src)), (
        "numpy.load(allow_pickle=True) is outside the rule's loader set — known scope gap, NOT flagged"
    )
