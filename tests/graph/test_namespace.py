"""Toplevel-relative ids vs one partition's view of them (spec 5.2)."""

import pytest

from auditor.graph.refine.namespace import in_scope, to_partition


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
