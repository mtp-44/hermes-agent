"""Nested ``$(`` must not make command detection super-linear.

``_scan_dollar_paren_end`` used to rescan to the end of the string from every
``$(``, and ``_replace_simple_command_substitutions`` re-scanned and
re-tokenised the rest of the word at every level: 400 levels of ``$(``
(1.2 KB) took ~16 s per detection, ``"$(e" * 400`` ~7 s and 3 KB ~100 s, all
holding the GIL. Both are now bounded; these tests pin that, and pin that the
faster code returns exactly what the original did.
"""
import random
import subprocess
import sys

import pytest

from tools import approval


def _reference_scan_dollar_paren_end(command: str, start: int):
    """The original, uncached balanced ``$(...)`` scanner."""
    depth = 1
    quote = None
    i = start + 2
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "\\" and i + 1 < len(command):
            i += 2
            continue
        if command.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if ch == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return i
            continue
        i += 1
    return None


def _reference_replace_simple_command_substitutions(word: str) -> str:
    """The original whole-body substitution rewrite."""
    chars = []
    i = 0
    while i < len(word):
        if word.startswith("$(", i):
            end = _reference_scan_dollar_paren_end(word, i)
            if end is not None:
                replacement = approval._literal_command_substitution_output(word[i + 2:end - 1])
                if replacement is not None:
                    chars.append(replacement)
                    i = end
                    continue
        if word[i] == "`":
            end = approval._scan_backtick_end(word, i)
            if end is not None:
                replacement = approval._literal_command_substitution_output(word[i + 1:end - 1])
                if replacement is not None:
                    chars.append(replacement)
                    i = end
                    continue
        chars.append(word[i])
        i += 1
    return "".join(chars)


_ALPHABET = list("$()`'\"\\ x;|\t") + [
    "$(", "$(e", "))", "')'", '")"', "\\)", "\\$(", "\\`",
    "echo ", "printf ", "printf %s ", "echo -n ", "rm", "$(echo rm)", "`echo rm`",
]


def _random_words(seed: int, count: int, max_len: int):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, max_len)))


def test_memoised_scanner_matches_reference_in_any_call_order():
    rng = random.Random(1)
    for word in _random_words(seed=2, count=20_000, max_len=30):
        starts = [i for i in range(len(word)) if word.startswith("$(", i)]
        rng.shuffle(starts)  # memo is shared across calls: order must not matter
        for start in starts:
            assert approval._scan_dollar_paren_end(word, start) == _reference_scan_dollar_paren_end(word, start), (word, start)


def test_substitution_rewrite_matches_reference():
    for word in _random_words(seed=3, count=20_000, max_len=28):
        assert approval._replace_simple_command_substitutions(word) == _reference_replace_simple_command_substitutions(word), word


@pytest.mark.parametrize(
    "command",
    [
        "$(echo rm) -rf /tmp/x",
        "`echo rm` -rf /tmp/x",
        "$( printf rm ) -rf /tmp/x",
        "$(printf %s rm) -rf /tmp/x",
        "$(\\echo rm) -rf /tmp/x",
        "$('echo' rm) -rf /tmp/x",
        "$(echo 'r'm) -rf /tmp/x",
    ],
)
def test_literal_substitution_deobfuscation_still_detects(command):
    assert approval.detect_dangerous_command(command)[0] is True


@pytest.mark.parametrize(
    "expr",
    [
        '"echo " + "$(" * 400 + "x" + ")" * 400',
        '"$(e" * 400 + "x" + ")" * 400',
        '"$(e" * 400',
        '"$(" * 400',
        '"$(echo " * 400 + "x" + ")" * 400',
    ],
)
def test_nested_substitution_detection_is_bounded(expr):
    # Before: 7-22 s each. Subprocess + timeout so a regression can't hang
    # pytest on the GIL; the in-process bound is generous for slow CI hosts.
    code = f"""
import time
from tools.approval import _match_user_deny_rule, detect_dangerous_command
command = {expr}
start = time.perf_counter()
detect_dangerous_command(command)
_match_user_deny_rule(command)
assert time.perf_counter() - start < 4.0, time.perf_counter() - start
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
