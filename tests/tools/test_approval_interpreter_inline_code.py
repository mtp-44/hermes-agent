"""Interpreter inline-code forms the plain `-c`/`-e` rules missed.

Local widening, a behaviour subset of upstream b90dbac1d6 (which unifies
execution-bearing option detection with a ~570-line parser this fork does not
carry): options before the flag, combined short flags, versioned names, the
long spellings, and `-` (program from stdin) before a heredoc.
"""
import subprocess
import sys

import pytest

from tools.approval import detect_dangerous_command

_EXEC = "script execution via -e/-c flag"
_SHELL = "shell command via -c/-lc flag"
_HEREDOC = "script execution via heredoc"


@pytest.mark.parametrize(("command", "key"), [
    ("python3.12 -c 'print(1)'", _EXEC),
    ("python2.7 -c 'print 1'", _EXEC),
    ("python -I -c 'print(1)'", _EXEC),
    ("python3 -Bc 'print(1)'", _EXEC),
    ("python3 -u -B -c 'print(1)'", _EXEC),
    ("python3 -W ignore -c 'print(1)'", _EXEC),
    ("/usr/bin/python3.11 -Ic 'print(1)'", _EXEC),
    ('python3 -c"print(1)"', _EXEC),
    ("sudo python3 -I -c 'print(1)'", _EXEC),
    ("node --eval 'console.log(1)'", _EXEC),
    ("node --eval='console.log(1)'", _EXEC),
    ("node -p '1+1'", _EXEC),
    ("node --print '1+1'", _EXEC),
    ("node -pe '1+1'", _EXEC),
    ("node --no-warnings -e 'console.log(1)'", _EXEC),
    ("node -r ts-node/register -e 'console.log(1)'", _EXEC),
    ("perl -we 'print 1'", _EXEC),
    ("perl -lane 'print $F[0]' f.txt", _EXEC),
    ("perl -Mstrict -e 'print 1'", _EXEC),
    ("perl -e'print 1'", _EXEC),
    ("ruby -w -e 'puts 1'", _EXEC),
    ("ruby -ne 'puts $_' f.txt", _EXEC),
    ("ruby -I lib -e 'puts 1'", _EXEC),
    ("bash -o pipefail -c 'ls'", _SHELL),
    ("bash --norc -c 'ls'", _SHELL),
    ("bash --rcfile /tmp/rc -ic 'ls'", _SHELL),
    ("zsh -f -c 'ls'", _SHELL),
    ("sh -e -c 'ls'", _SHELL),
    ("set -e; bash +o history -c 'ls'", _SHELL),
    ("python3 - <<EOF\nprint(1)\nEOF", _HEREDOC),
    ("python3 -u - <<'EOF'\nprint(1)\nEOF", _HEREDOC),
    ("python3.12 <<EOF\nprint(1)\nEOF", _HEREDOC),
])
def test_inline_code_forms_require_approval(command, key):
    dangerous, got_key, _ = detect_dangerous_command(command)
    assert dangerous is True, command
    assert got_key == key, (command, got_key)


@pytest.mark.parametrize("command", [
    "python3 script.py",
    "python3 -u script.py --verbose",
    "python3 script.py -c config.yaml",
    "python -m pytest",
    "python -m pytest -c pytest.ini",
    "python3 -m pip install -e .",
    "python3 -W ignore script.py",
    "python3 --version",
    "python-config --cflags",
    "node app.js",
    "node app.js -e production",
    "node --inspect app.js",
    "node -r dotenv/config app.js",
    "node --version",
    "perl script.pl -e",
    "perl -v",
    "ruby script.rb -e",
    "ruby -v",
    "bash script.sh",
    "bash -x ./run.sh --clean",
    "bash -o pipefail ./run.sh",
    "./deploy.sh -v -c conf.yaml",
    "python3 script.py <<EOF\ndata\nEOF",
    # Note: like the original unanchored `-c`/`-e` rule, prose that spells the flag
    # (`grep 'python -c ' docs/`) still prompts; that is unchanged from main.
])
def test_ordinary_interpreter_runs_stay_unprompted(command):
    assert detect_dangerous_command(command) == (False, None, None), command


def test_option_loops_do_not_backtrack_catastrophically():
    code = '''
import time
from tools.approval import detect_dangerous_command
cases = ["bash " + "-o " * 5000 + "x", "python3 " + "-W " * 5000 + "x", "node " + "-r " * 5000 + "x",
         "perl " + "-I " * 5000 + "x", "bash " + "-o x " * 3000 + "y"]
start = time.perf_counter()
for cmd in cases:
    detect_dangerous_command(cmd)
assert time.perf_counter() - start < 3.0
'''
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)
