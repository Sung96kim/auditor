"""Shell style detectors: SH-STYLE-LONG-COMMENT fires on long prose blocks, skips headers,
shebangs, and shellcheck directives."""

from _support import rule_ids, run_sh_audit


def test_long_comment_flags_prose_block():
    src = (
        "#!/bin/bash\n"
        "echo start\n"
        "# first we set up the directory\n"
        "# then we copy the files across\n"
        "# after that we fix permissions\n"
        "# finally we print a summary\n"
        "echo done\n"
    )
    assert "SH-STYLE-LONG-COMMENT" in rule_ids(run_sh_audit(src))


def test_long_comment_skips_header_and_shebang():
    src = (
        "#!/bin/bash\n"
        "# install script for the widget\n"
        "# usage: ./install.sh\n"
        "# requires root\n"
        "# tested on ubuntu\n"
        "set -euo pipefail\n"
    )
    assert "SH-STYLE-LONG-COMMENT" not in rule_ids(run_sh_audit(src))


def test_long_comment_skips_shellcheck_directives():
    src = (
        "echo hi\n"
        "# shellcheck disable=SC2086\n"
        "# shellcheck disable=SC2046\n"
        "# shellcheck disable=SC2016\n"
        "# shellcheck disable=SC1090\n"
        "run_thing\n"
    )
    assert "SH-STYLE-LONG-COMMENT" not in rule_ids(run_sh_audit(src))


def test_long_comment_counts_only_prose_in_bash():
    src = (
        "echo start\n"
        "# we first probe the environment to decide\n"
        "# which package manager to drive here\n"
        "# apt-get install -y curl\n"
        "# shellcheck disable=SC2086\n"
        "# then we verify the binary landed ok\n"
        "# and finally we symlink it into place\n"
        "run\n"
    )
    # `shellcheck` is a directive; `apt-get install -y curl` has no shell *syntax* so it stays
    # prose. 5 prose lines > 3 -> flagged. (If apt-get carried `$VAR`/`&&`/a redirect it would be
    # excluded as commented-out shell — detection keys on grammar, not the command name.)
    assert "SH-STYLE-LONG-COMMENT" in rule_ids(run_sh_audit(src))


def test_long_comment_skips_commented_out_command_block():
    src = (
        "echo hi\n"
        "# FOO=/opt/app\n"
        "# export PATH=$FOO/bin:$PATH\n"
        "# cd $FOO && ./configure\n"
        "# make && make install\n"
        "run\n"
    )
    assert "SH-STYLE-LONG-COMMENT" not in rule_ids(run_sh_audit(src))
