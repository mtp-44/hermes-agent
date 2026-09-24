"""Long secret-keyword runs must not stall the redactor.

The config/YAML key patterns (_CFG_DOTTED_RE, _CFG_ANCHORED_RE, _YAML_ASSIGN_RE,
_YAML_QUOTED_ASSIGN_RE) had a backtrackable ``<class>*`` on both sides of the
keyword, so a run like ``"token" * N`` retried every keyword occurrence with a
fresh scan to the end of the run: quadratic (``token`` * 10k = 3 s at 50 KB) and
cubic for dotted runs (``api.key`` * N was ~2 s at 5 KB and ~117 s at 20 KB).
The redactor runs on every log line and outbound gateway message while holding
the GIL, so each shape runs in a subprocess whose timeout bounds a regression
without hanging pytest.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_KEYWORDS = ["token", "secret", "password", "passwd", "credential", "auth",
             "api_key", "apikey", "api.key", "API KEY", "a.token"]

# name -> (python expression over ``run`` building the text, redact kwargs)
_SHAPES = {
    "assign_spaced": ("run + ' = x'", "{}"),
    "assign_no_value": ("run + '= '", "{}"),
    "dotted_prefix": ("'a.' + run + ' = x'", "{}"),
    "dotted_suffix": ("run + '. = x'", "{}"),
    "yaml_spaced": ("'  ' + run + ' : x'", "{}"),
    "yaml_no_value": ("run + ':'", "{}"),
    "yaml_quoted": ("'  ' + run + ' : \"x\"'", "{'secret_file': True}"),
    "yaml_quoted_open": ("run + ': \"x'", "{'secret_file': True}"),
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_50kb_keyword_run_is_bounded(shape):
    expr, kwargs = _SHAPES[shape]
    code = f'''
import time
from agent.redact import redact_sensitive_text
for kw in {_KEYWORDS!r}:
    run = kw * (50_000 // len(kw))
    text = {expr}
    start = time.perf_counter()
    redact_sensitive_text(text, force=True, **{kwargs})
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, (kw, elapsed)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_REPO_ROOT), env.get("PYTHONPATH", "")) if p)
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20,
                   cwd=_REPO_ROOT, env=env)


def test_keyword_run_values_still_masked():
    # The linear forms must not stop masking the real shapes they guard.
    from agent.redact import redact_sensitive_text

    for text, secret in (
        ("tokentoken=hunter2", "hunter2"),
        ("app.api.key=hunter2", "hunter2"),
        ("a.tokena.token=hunter2", "hunter2"),
        ("api key.name=hunter2", "hunter2"),
        ("xapi key.y=hunter2", "hunter2"),
        ("export api key=hunter2", "hunter2"),
        ("  passwordpassword: hunter2", "hunter2"),
    ):
        assert secret not in redact_sensitive_text(text, force=True), text
    quoted = redact_sensitive_text('  api_keyapi_key: "hunter2"', force=True, secret_file=True)
    assert "hunter2" not in quoted
