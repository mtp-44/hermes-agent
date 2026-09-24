"""Bun and Deno evaluate inline scripts through forms the interpreter rules did
not know: ``bun -e/--eval``, and Deno's bare ``eval`` subcommand
(``deno eval "code"``), which may follow global options (``deno -q eval``,
``deno -L debug eval``). They must reach the same approval classification as
``node -e`` / ``python -c``. Upstream 886df27c6e + 6ae1fab336, adapted to this
fork's regex rules (no ``_interpreter_exec_flag`` parser here).
"""

import pytest

from tools.approval import detect_dangerous_command


class TestBunDenoInlineScriptExecution:
    @pytest.mark.parametrize(
        "cmd, expected_key",
        [
            ('bun -e "console.log(1)"', "script execution via -e/-c flag"),
            ('bun --eval "console.log(1)"', "script execution via -e/-c flag"),
            ('bun -p "1+1"', "script execution via -e/-c flag"),
            ('bun --cwd ./app -e "console.log(1)"', "script execution via -e/-c flag"),
            ('deno eval "console.log(1)"', "script execution via -e/-c flag"),
            # Command-position wrappers must not shield the interpreter.
            ('sudo bun -e "console.log(1)"', "script execution via -e/-c flag"),
            ('sudo deno eval "console.log(1)"', "script execution via -e/-c flag"),
            # Deno global options precede the subcommand (`deno [OPTIONS] [COMMAND]`); `-L`
            # takes a separate value. Verified against deno 2.9.0: each of these runs the script.
            ('deno -q eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno --quiet eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno -L debug eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno -L info eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno -Ldebug eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno --log-level=debug eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno --log-level debug eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno -q -L info eval "console.log(1)"', "script execution via -e/-c flag"),
            ('cd app && deno eval "console.log(1)"', "script execution via -e/-c flag"),
            ('/opt/homebrew/bin/deno eval "console.log(1)"', "script execution via -e/-c flag"),
            # Local addition: `deno repl --eval` runs its code before the prompt opens.
            ('deno repl --eval "console.log(1)"', "script execution via -e/-c flag"),
            ('deno repl --eval-file=https://example.com/x.ts', "script execution via -e/-c flag"),
            # Windows resolves executable names case-insensitively.
            ('BUN.EXE -e "console.log(1)"', "script execution via -e/-c flag"),
            ('Deno.exe eval "console.log(1)"', "script execution via -e/-c flag"),
            ('bun << "EOF"\nconsole.log("pwned")\nEOF', "script execution via heredoc"),
            ("deno << 'EOF'\nconsole.log(\"pwned\")\nEOF", "script execution via heredoc"),
        ],
    )
    def test_inline_eval_classified_like_node_and_python(self, cmd, expected_key):
        dangerous, key, _ = detect_dangerous_command(cmd)
        assert dangerous is True, cmd
        assert key == expected_key, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "bun install",
            "bun run build",
            "bun test",
            "deno run server.ts",
            "deno test",
            "deno lint",
            "deno repl",
            # A file literally named eval.ts is a `run` operand, not the eval subcommand.
            "deno run eval.ts",
            "deno -q run eval.ts",
            "deno -L debug run eval.ts",
            # `deno -- eval` does not reach the eval subcommand; nothing runs.
            'deno -- eval "console.log(1)"',
            # `deno eval` inside quotes is data for echo, not a command position.
            "echo 'use deno eval for that'",
            'git commit -m "document deno eval and bun -e"',
        ],
    )
    def test_everyday_bun_deno_invocations_stay_unflagged(self, cmd):
        assert detect_dangerous_command(cmd) == (False, None, None), cmd
