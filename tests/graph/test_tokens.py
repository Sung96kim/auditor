import pytest

from auditor.graph.tokens import (
    TEXT_FLOOR,
    is_text_sparse,
    normalize_tokens,
    split_ident,
    symbol_document,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("loadUserProfile", ["load", "user", "profile"]),
        ("get_user_by_id", ["get", "user", "by", "id"]),
        ("HTTPServer", ["http", "server"]),
        ("__init_subclass__", ["init", "subclass"]),
    ],
)
def test_split_ident(raw, expected):
    assert split_ident(raw) == expected


def test_normalize_drops_stopwords_and_collapses_verbs():
    # get/fetch/load all collapse to the READ canonical; stopwords ('the','id') dropped
    out = normalize_tokens(["get", "fetch", "load", "the", "user", "id"])
    assert out.count("read") == 3  # get, fetch, load -> read
    assert "the" not in out and "id" not in out and "user" in out


def test_symbol_document_weights_declaration_3x():
    doc = symbol_document(
        name="build_payload",
        args=["schema"],
        docstring="",
        body_idents=["encode"],
        param_types=[],
        path_tokens=["models"],
        class_name=None,
    )
    # declaration tokens (build/payload/schema) appear 3x; body (encode) 1x; path (models) 1x
    assert doc.count("payload") == 3 and doc.count("encode") == 1 and "models" in doc


def test_text_sparse_floor_uses_full_cascade():
    # bare name + no docs/body, but path+class context lifts it above the floor
    doc = symbol_document(
        name="id",
        args=[],
        docstring="",
        body_idents=[],
        param_types=[],
        path_tokens=["broker", "base"],
        class_name="Execution",
    )
    assert not is_text_sparse(doc)  # broker, base, execution rescue it
    assert is_text_sparse(["only", "x"]) and TEXT_FLOOR == 4
