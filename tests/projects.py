"""Small sample projects (as {path: text} dicts) used by the Stage 2 tests."""

PASSING = {
    "util.py": "def add(a, b):\n    return a + b\n",
    "test_util.py": "from util import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
}

# Wrong implementation: the test expects 3 but add() subtracts.
FAILING = {
    "util.py": "def add(a, b):\n    return a - b\n",
    "test_util.py": "from util import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\ndef test_other():\n    assert add(5, 0) == 5\n",
}

# Unhandled ZeroDivisionError in project code, reached by two tests at the same line.
EXCEPTION = {
    "calc.py": "def divide(a, b):\n    return a / b\n",
    "test_calc.py": "from calc import divide\n\ndef test_a():\n    divide(1, 0)\n\ndef test_b():\n    divide(2, 0)\n\ndef test_ok():\n    assert divide(4, 2) == 2\n",
}

# main.py crashes when run; no tests at all.
ENTRY_CRASH = {
    "calc.py": "def divide(a, b):\n    return a / b\n",
    "main.py": "from calc import divide\n\nif __name__ == '__main__':\n    print(divide(1, 0))\n",
}

NO_TESTS = {"app_lib.py": "def hello():\n    return 'hi'\n"}

SYNTAX_ERROR = {
    "broken.py": "def oops(:\n    pass\n",
    "good.py": "def fine():\n    return 1\n",
    "test_good.py": "from good import fine\n\ndef test_fine():\n    assert fine() == 1\n",
}

# broken.py is imported by a test, so pytest collection fails too (a RUNTIME observation of the same problem).
SYNTAX_ERROR_IMPORTED = {
    "broken.py": "def oops(:\n    pass\n",
    "test_broken.py": "import broken\n\ndef test_x():\n    assert True\n",
}

# F821 (undefined name) in code that no test ever calls: only static analysis can see it.
STATIC_ONLY = {
    "lib.py": "def used():\n    return 1\n\ndef never_called():\n    return missing_name + 1\n",
    "test_lib.py": "from lib import used\n\ndef test_used():\n    assert used() == 1\n",
}

HANGING_TEST = {
    "slow.py": "def f():\n    return 1\n",
    "test_slow.py": "import time\nfrom slow import f\n\ndef test_fast():\n    assert f() == 1\n\ndef test_hangs():\n    while True:\n        time.sleep(0.1)\n",
}

HANGING_ENTRY = {
    "main.py": "import time\nwhile True:\n    time.sleep(0.1)\n",
}

NEEDS_INPUT_ENTRY = {
    "main.py": "name = input('name? ')\nprint(name)\n",
}

NEEDS_ARGS_ENTRY = {
    "main.py": "import argparse\np = argparse.ArgumentParser()\np.add_argument('path')\nprint(p.parse_args().path)\n",
}

# Tests that WRITE FILES into the working directory: the original upload must stay untouched.
WRITES_FILES = {
    "data.txt": "original contents\n",
    "util.py": "def f():\n    return 1\n",
    "test_writes.py": (
        "def test_writes():\n"
        "    open('created_by_test.txt', 'w').write('x')\n"
        "    open('data.txt', 'a').write('MODIFIED\\n')\n"
        "    open('util.py', 'w').write('# overwritten\\n')\n"
    ),
}

# Tests that assert the server's secrets are NOT visible to project code.
ENV_PROBE = {
    "test_env.py": (
        "import os\n\n"
        "def test_no_secrets():\n"
        "    for name in ('GEMINI_API_KEY', 'MONGODB_URI', 'SECRET_KEY', 'AP_TEST_TOKEN'):\n"
        "        assert name not in os.environ, name\n"
    ),
}

# Missing third-party package: an environment problem, not a project bug.
MISSING_PACKAGE = {
    "uses_pkg.py": "import definitely_not_installed_pkg_xyz\n",
    "test_uses_pkg.py": "import uses_pkg\n\ndef test_x():\n    assert True\n",
}


# A hostile test that tries to modify the ORIGINAL upload through a relative path
# (cwd is DATA_DIR/runs/<run>/work, so ../../../projects/*/original is the stored upload).
# Process isolation does NOT stop this: it is not a hardened sandbox. The integrity digest must DETECT it.
TAMPER_ORIGINAL = {
    "util.py": "def f():\n    return 1\n",
    "test_tamper.py": (
        "import glob\n\n"
        "def test_tamper():\n"
        "    for path in glob.glob('../../../projects/*/original/util.py'):\n"
        "        open(path, 'w').write('# tampered\\n')\n"
    ),
}
