"""The hardline floor judges shell quoting on the AUTHOR's text, not on normalized text.

``_normalize_command_for_detection`` strips backslash escapes so ``r\\m`` cannot hide ``rm``. That
is right for pattern matching but wrong for quote tracking: ``\\"`` becomes ``"`` and flips quote
parity, so in ``echo "a\\"b"; (reboot)`` the subshell start sat "inside" a phantom quote, no start
was marked, and the floor let it through. A faithful variant marks command starts on the raw
command before normalizing. Upstream e383c28d2f, adapted: this fork has no malformed-quoting
verdict, and its flat ``_CMDPOS`` still treats ``;``/``&``/``|`` inside quotes as separators, so
upstream's ``grep -n "; reboot" f.txt`` negative (fixed by b90dbac1d6, not ported) is left out.
"""
import pytest

from tools.approval import (
    _command_detection_variants,
    _iter_shell_command_starts,
    detect_dangerous_command,
    detect_hardline_command,
)


@pytest.mark.parametrize("command", [
    'grep -o "[^\\"]*" f',
    r'grep -n "^from\|^__all__\|^    \"" tools/environments/__init__.py | head -15',
    r'egrep -n "alpha\"beta|gamma" input.txt',
    r'grep -v "^./tests/\|def \|\"\"\"" x.py | grep -v "read_only=True"',
    'echo "value is ${HOME}/x"',
])
def test_escaped_quotes_in_a_valid_quoted_argument_are_not_flagged(command):
    assert detect_hardline_command(command) == (False, None)
    assert detect_dangerous_command(command) == (False, None, None)


@pytest.mark.xfail(strict=True, reason=(
    "Known fail-safe false positive, on main and upstream alike: the variant that marks starts on "
    "NORMALIZED text still sees the phantom quote and splits the quoted '(' / '{'."
))
@pytest.mark.parametrize("command", [r'echo "a\"b (reboot)"', r'echo "a\"b { reboot; }"'])
def test_quoted_subshell_text_after_escaped_quote_is_still_over_flagged(command):
    assert detect_hardline_command(command) == (False, None)


@pytest.mark.parametrize(("command", "description"), [
    (r'cat "f\"n.txt"; rm -rf --no-preserve-root /', "recursive delete of root filesystem"),
    (r'echo "a\"b"; reboot', "system shutdown/reboot"),
    (r'echo "a\"b" && rm -rf ~', "recursive delete of home directory"),
    (r'echo "a\"b"; rm${IFS}-rf${IFS}/', "recursive delete of root filesystem"),
    (r'grep -n "prefix \"quoted\" suffix" input.txt; reboot', "system shutdown/reboot"),
    ("printf \\\\\nreboot", "system shutdown/reboot"),
    # Subshell / brace-group starts after an escaped quote: missed before the faithful variant.
    (r'echo "a\"b"; (reboot)', "system shutdown/reboot"),
    (r'cat "f\"n.txt"; { reboot; }', "system shutdown/reboot"),
    (r'echo "a\"b" && (rm -rf /)', "recursive delete of root filesystem"),
    (r'echo "x\"y"; { shutdown -h now; }', "system shutdown/reboot"),
])
def test_escaped_quote_before_a_hardline_command_does_not_hide_it(command, description):
    assert detect_hardline_command(command) == (True, description)


def test_parameter_expansion_brace_is_not_a_command_start():
    command = 'rm${IFS}-rf${IFS}/tmp/x'
    brace = command.index("{")
    assert brace + 1 not in set(_iter_shell_command_starts(command))
    # A real brace group still is.
    assert 2 in set(_iter_shell_command_starts("{ reboot; }"))


def test_faithful_variant_is_yielded():
    variants = list(_command_detection_variants(r'echo "a\"b"; (reboot)'))
    assert any("\nreboot" in v for v in variants), variants
