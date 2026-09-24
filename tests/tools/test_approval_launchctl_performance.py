"""Long non-launchctl inputs must not starve other Gateway threads.

The order-independent launchctl rule is two whole-input lookaheads; without
the ``\\A`` anchor ``re.search`` re-runs them at every offset, which is
quadratic and holds the GIL. A subprocess timeout bounds a regression
without hanging pytest on the GIL.
"""
import subprocess
import sys

_DESC = "stop/restart hermes launchd service (kills running agents)"


def test_launchctl_guard_long_negative_input_is_bounded():
    code = f'''
from tools.approval import DANGEROUS_PATTERNS_COMPILED
rules = [(rx, desc) for rx, desc in DANGEROUS_PATTERNS_COMPILED if desc == {_DESC!r}]
assert len(rules) == 1
rx = rules[0][0]
assert rx.search("x" * 100_000) is None
assert rx.search("launchctl list " * 20_000) is None
assert rx.search("hermes " * 20_000) is None
'''
    subprocess.run([sys.executable, "-c", code], check=True, timeout=5)


def test_detect_dangerous_command_long_negative_input_is_bounded():
    code = '''
import time
from tools.approval import detect_dangerous_command
start = time.perf_counter()
assert detect_dangerous_command("x" * 100_000) == (False, None, None)
assert time.perf_counter() - start < 3.0
'''
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)
