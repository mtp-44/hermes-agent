"""A heredoc of quoted lines must not make detection quadratic (upstream #113535, 4eff83cdec).

``_command_detection_variants`` used to yield one FULL-LENGTH variant per quoted or escaped
command word. A heredoc body of quoted lines (``"key": "value"`` ...) is hundreds of quoted
command words, so both detection passes scanned O(words * len) characters: 16 KB took ~0.7 s
hardline + ~3.9 s dangerous, 33 KB ~3 s + ~16 s, all holding the GIL on the gateway loop. Now
every command word is deobfuscated into ONE combined variant (one per nesting level), and the
start marker splices in one pass. Subprocess + timeout so a regression cannot hang pytest.
"""
import subprocess
import sys

import pytest

from tools.approval import _command_detection_variants, detect_dangerous_command


def _quoted_lines(n: int) -> str:
    return "\n".join(f'"key{i}": "line {i} with some text"' for i in range(n))


def _heredoc(n: int) -> str:
    return "cat > out.json <<'EOF'\n" + _quoted_lines(n) + "\nEOF"


def test_variant_count_does_not_grow_with_quoted_command_words():
    # Before: 462 variants for 460 quoted lines (one per quoted command word).
    assert len(list(_command_detection_variants(_heredoc(460)))) <= 6
    assert len(list(_command_detection_variants(_heredoc(940)))) <= 6


@pytest.mark.parametrize("lines", [460, 940])  # ~16 KB and ~33 KB
def test_large_quoted_heredoc_detection_is_bounded(lines):
    code = f'''
import time
from tools.approval import detect_dangerous_command, detect_hardline_command
body = "\\n".join(f'"key{{i}}": "line {{i}} with some text"' for i in range({lines}))
cmd = "cat > out.json <<'EOF'\\n" + body + "\\nEOF"
start = time.perf_counter()
assert detect_hardline_command(cmd) == (False, None)
assert detect_dangerous_command(cmd) == (False, None, None)
elapsed = time.perf_counter() - start
assert elapsed < 3.0, f"detection took {{elapsed:.2f}}s for {{len(cmd)}} chars"
'''
    # Before: ~4.6 s (16 KB) and ~19 s (33 KB).
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


def test_many_quoted_command_words_without_heredoc_are_bounded():
    code = '''
import time
from tools.approval import detect_dangerous_command, detect_hardline_command
cmd = "\\n".join(f'"key{i}": "line {i} with some text"' for i in range(460))
start = time.perf_counter()
assert detect_hardline_command(cmd) == (False, None)
assert detect_dangerous_command(cmd) == (False, None, None)
assert time.perf_counter() - start < 3.0
'''
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


def test_obfuscated_command_words_still_detected_when_merged_into_one_variant():
    cmd = 'echo "one"; $(echo rm) -rf ~/.ssh; echo "two"; r\'\'m -rf ~/.gnupg'
    dangerous, _, desc = detect_dangerous_command(cmd)
    assert dangerous is True
    assert "delete" in desc.lower(), desc
    cmd = 'echo "one"; echo "two"; "r"m -rf ~/.gnupg; echo "three"'
    dangerous, _, desc = detect_dangerous_command(cmd)
    assert dangerous is True
    assert "delete" in desc.lower(), desc


def test_nested_command_word_spans_land_in_separate_rounds():
    # The substitution word and the command word inside it overlap, so they cannot share a variant;
    # the inner one must land in a second-round variant instead of being dropped.
    variants = list(_command_detection_variants('$(e"cho" "r"m) -rf ~/.ssh'))
    assert "rm -rf ~/.ssh" in variants, variants
    assert '$(echo "r"m) -rf ~/.ssh' in variants, variants
