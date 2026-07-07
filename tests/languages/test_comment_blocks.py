"""Unit tests for the language-neutral long-comment analyzer."""

from auditor.languages.comment_blocks import (
    CommentBlock,
    CommentBlockAnalyzer,
    PythonCommentBlocks,
    ShellCommentBlocks,
)


class _Hashes(CommentBlockAnalyzer):
    """Test analyzer: every `#`-leading line is a comment; no directives, no code detection —
    isolates the core grouping / preamble / threshold logic."""

    def comment_lines(self, source: str, lines: list[str]) -> set[int]:
        return {i for i, line in enumerate(lines, 1) if line.lstrip().startswith("#")}


def _blocks(analyzer: CommentBlockAnalyzer, lines: list[str], threshold: int = 3):
    return analyzer.blocks("\n".join(lines), lines, threshold=threshold)


# --- core algorithm (via the neutral _Hashes analyzer) ---


def test_flags_when_prose_exceeds_threshold():
    lines = ["x = 1", "# a", "# b", "# c", "# d", "y = 2"]
    assert _blocks(_Hashes(), lines) == [CommentBlock(2, 4)]


def test_no_flag_at_threshold():
    lines = ["x = 1", "# a", "# b", "# c", "y = 2"]
    assert _blocks(_Hashes(), lines) == []


def test_blank_line_splits_run():
    lines = ["x = 1", "# a", "# b", "", "# c", "# d"]
    assert _blocks(_Hashes(), lines) == []


def test_preamble_run_dropped():
    lines = ["# lic 1", "# lic 2", "# lic 3", "# lic 4", "# lic 5", "import os"]
    assert _blocks(_Hashes(), lines) == []


def test_empty_comment_spacer_not_prose():
    # a bare `#` continues the run but is not prose: 3 real prose lines <= threshold -> no flag
    lines = ["x = 1", "# heading", "#", "# body one", "#", "# body two", "y = 2"]
    assert _blocks(_Hashes(), lines) == []


def test_url_and_table_lines_not_prose():
    lines = [
        "x = 1",
        "# https://example.com/a",
        "# https://example.com/b",
        "# | col1 | col2 |",
        "# ------|-------",
        "y = 2",
    ]
    assert _blocks(_Hashes(), lines) == []


# --- Python front-end (tokenize + ast) ---


def test_directive_lines_not_prose():
    lines = [
        "x = 1",
        "# noqa: E501",
        "# type: ignore",
        "# pragma: no cover",
        "# mypy: ignore",
        "y = 2",
    ]
    assert _blocks(PythonCommentBlocks(), lines) == []


def test_anchor_is_first_prose_line():
    # a leading directive line is skipped; prose (multi-word so it can't parse as code) starts at 3
    lines = [
        "x = 1",
        "# noqa: E501",
        "# first real explanation line",
        "# second real explanation line",
        "# third real explanation line",
        "# fourth real explanation line",
        "y = 2",
    ]
    assert _blocks(PythonCommentBlocks(), lines) == [CommentBlock(3, 4)]


def test_commented_out_code_excluded():
    lines = [
        "x = 1",
        "# def g(n):",
        "#     return n * 2",
        "# g(3)",
        "# print(g)",
        "y = 2",
    ]
    assert _blocks(PythonCommentBlocks(), lines) == []


def test_standalone_ignores_hash_in_string():
    a = PythonCommentBlocks()
    src = 'template = """\n# not a comment\n# also not\n"""\nx = 1  # trailing\n'
    assert a.comment_lines(src, src.splitlines()) == set()


def test_standalone_collects_own_line_comments():
    a = PythonCommentBlocks()
    src = "# one\nx = 1\n    # two\n"
    assert a.comment_lines(src, src.splitlines()) == {1, 3}


def test_malformed_source_does_not_crash():
    # an unterminated triple-quoted string raises TokenError inside tokenize; comment_lines swallows
    # it and yields nothing rather than crashing the scan.
    a = PythonCommentBlocks()
    assert (
        a.comment_lines('x = """open\n# c1\n# c2\n', ['x = """open', "# c1", "# c2"])
        == set()
    )


def test_python_code_indices_flags_commented_out_block():
    a = PythonCommentBlocks()
    bodies = ["def helper(x):", "    return x + 1", "value = helper(2)", "print(value)"]
    assert a.code_indices(bodies) == {0, 1, 2, 3}


def test_python_code_indices_ignores_prose():
    a = PythonCommentBlocks()
    assert (
        a.code_indices(["first we parse", "then we validate", "then we store"]) == set()
    )


def test_python_code_indices_ignores_bare_word_list():
    a = PythonCommentBlocks()
    assert a.code_indices(["process", "transform", "store", "done"]) == set()


def test_python_code_indices_ignores_bare_literal_list():
    a = PythonCommentBlocks()
    assert a.code_indices(["1", "2", "3", "4"]) == set()
    assert a.code_indices(['"term one"', '"term two"', '"term three"']) == set()


def test_bare_number_list_is_prose_not_code():
    # a pure numbered/enumerated comment block is prose, not commented-out code
    lines = ["x = 1", "# 1", "# 2", "# 3", "# 4", "y = 2"]
    assert _blocks(PythonCommentBlocks(), lines) == [CommentBlock(2, 4)]


# --- shell front-end (line-scan + syntax) ---


def test_shell_comment_lines_skip_shebang():
    a = ShellCommentBlocks()
    lines = ["#!/bin/bash", "# real comment", "echo hi"]
    assert a.comment_lines("\n".join(lines), lines) == {2}


def test_shell_code_indices_marks_shell_syntax_not_command_names():
    a = ShellCommentBlocks()
    bodies = [
        "FOO=/opt/app",  # assignment -> code (0)
        "cd $HOME && ls",  # $var + && -> code (1)
        "a real sentence about the job",  # prose -> not code
        "echo done >> run.log",  # append redirect -> code (3)
        "apt-get install -y curl",  # bare command, no shell syntax -> stays prose
    ]
    assert a.code_indices(bodies) == {0, 1, 3}


# --- complex / adversarial cases ---


def test_realistic_mixed_block_counts_only_prose():
    lines = [
        "def handler():",
        "# This function validates the incoming",
        "# payload before we hand it downstream.",
        "# noqa: E501",
        "# See https://example.com/spec for details",
        "# and https://example.com/appendix too.",
        "# ----------------------------------------",
        "# The tricky part is the retry accounting,",
        "# which must survive a mid-batch crash,",
        "# so we checkpoint after every write.",
        "    return None",
    ]
    # 2 prose + directive + 2 url + divider + 3 prose = 5 prose lines, anchor at first prose (2)
    assert _blocks(PythonCommentBlocks(), lines) == [CommentBlock(2, 5)]


def test_prose_interleaved_with_code_stays_flagged():
    # a run mixing prose and commented-out code does NOT parse as one module, so code_indices
    # excludes nothing and the prose (correctly) stays verbose.
    lines = [
        "x = 1",
        "# we short-circuit the empty case here:",
        "# if not items:",
        "#     return []",
        "# otherwise we accumulate below",
        "y = 2",
    ]
    assert _blocks(PythonCommentBlocks(), lines) == [CommentBlock(2, 4)]


def test_multiple_blocks_only_long_one_flagged():
    lines = [
        "import os",
        "# short note here",
        "# still short",
        "def a():",
        "    return 1",
        "# a genuinely long block starts",
        "# spanning several lines of prose",
        "# that keeps going and going",
        "# well past the threshold now",
        "def b():",
        "    return 2",
    ]
    assert _blocks(_Hashes(), lines) == [CommentBlock(6, 4)]


def test_unicode_prose_counts_and_is_not_mistaken_for_a_table():
    lines = [
        "x = 1",
        "# esta función procesa los datos",
        "# validando cada campo requerido",
        "# antes de guardarlos en la base",
        "# de datos principal del sistema",
        "y = 2",
    ]
    assert _blocks(_Hashes(), lines) == [CommentBlock(2, 4)]


def test_divider_only_block_never_flagged():
    lines = ["x = 1", "# ======", "# ======", "# ------", "# ~~~~~~", "y = 2"]
    assert _blocks(_Hashes(), lines) == []


def test_all_comment_file_is_treated_as_preamble():
    lines = ["# line " + str(i) for i in range(10)]
    assert _blocks(_Hashes(), lines) == []


def test_prose_starting_with_pragma_lookalike_is_flagged():
    # bare "pragma" must not swallow prose words like "pragmatic"
    lines = [
        "x = 1",
        "# pragmatic choices were made here",
        "# pragmatic again in this line",
        "# pragmatic once more for good",
        "# pragmatic finally to close it",
        "y = 2",
    ]
    assert _blocks(PythonCommentBlocks(), lines) == [CommentBlock(2, 4)]
