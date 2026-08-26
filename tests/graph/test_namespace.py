"""Toplevel-relative ids vs one partition's view of them (spec 5.2)."""

import pytest

from auditor.graph.refine.namespace import (
    in_scope,
    scope_path,
    to_partition,
    under_scope,
)


@pytest.mark.parametrize(
    "node_id, prefix, stored",
    [
        ("m.py::f", "", "m.py::f"),
        ("m.py::f", "apps/backend/", "apps/backend/m.py::f"),
        ("pkg/m.py::C.method", "apps/backend/", "apps/backend/pkg/m.py::C.method"),
    ],
)
def test_a_stored_id_maps_back_into_the_partition(node_id, prefix, stored):
    assert to_partition(stored, prefix) == node_id


def test_an_id_outside_the_prefix_is_out_of_scope():
    """A second partition of the same checkout must neither apply nor stale its neighbour's work."""
    assert to_partition("apps/frontend/m.py::f", "apps/backend/") is None
    assert in_scope("apps/frontend/m.py::f", "apps/backend/") is False
    assert in_scope("apps/backend/m.py::f", "apps/backend/") is True


def test_a_root_scan_sees_every_id():
    assert to_partition("apps/backend/m.py::f", "") == "apps/backend/m.py::f"
    assert in_scope("anything", "") is True


@pytest.mark.parametrize(
    ("given", "wanted"),
    [
        (".", ""),
        ("./", ""),
        ("", ""),
        ("  ", ""),
        ("auditor", "auditor"),
        ("auditor/", "auditor"),
        ("./auditor", "auditor"),
        ("./auditor/cli/", "auditor/cli"),
    ],
)
def test_a_scope_is_normalised_to_a_prefix_a_node_id_can_start_with(given, wanted):
    """No node id starts with `./`, so a scope that keeps one briefs nothing, refuses every
    proposal and still exits 0. Shell completion produces exactly that."""
    assert scope_path(given) == wanted


def test_a_dot_scope_means_the_whole_repo():
    assert under_scope("helper.py::f", scope_path(".")) is True
    assert under_scope("auditor/cli/x.py::f", scope_path("./auditor")) is True
