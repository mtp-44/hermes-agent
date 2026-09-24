"""Regex-based secret redaction for logs and tool output.

Applies pattern matching to mask API keys, tokens, and credentials
before they reach log files, verbose output, or gateway logs.

Short tokens (< 18 chars) are fully masked. Longer tokens preserve
the first 6 and last 4 characters for debuggability.
"""

import bisect
import logging
import os
import re
import shlex

from agent.file_safety import _BLOCKED_PROJECT_ENV_BASENAMES as _ENV_FILE_BASENAMES

logger = logging.getLogger(__name__)

# Sensitive query-string parameter names (case-insensitive exact match).
# Ported from nearai/ironclaw#2529 — catches tokens whose values don't match
# any known vendor prefix regex (e.g. opaque tokens, short OAuth codes).
_SENSITIVE_QUERY_PARAMS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "auth",
    "jwt",
    "session",
    "secret",
    "key",
    "code",           # OAuth authorization codes
    "signature",      # pre-signed URL signatures
    "x-amz-signature",
})

# Sensitive form-urlencoded / JSON body key names (case-insensitive exact match).
# Exact match, NOT substring — "token_count" and "session_id" must NOT match.
# Ported from nearai/ironclaw#2529.
_SENSITIVE_BODY_KEYS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "auth",
    "jwt",
    "secret",
    "private_key",
    "authorization",
    "key",
})

# Snapshot at import time so runtime env mutations (e.g. LLM-generated
# `export HERMES_REDACT_SECRETS=false`) cannot disable redaction
# mid-session.  ON by default — secure default per issue #17691. Users who
# need raw credential values in tool output (e.g. working on the redactor
# itself) can opt out via `security.redact_secrets: false` in config.yaml
# (bridged to this env var in hermes_cli/main.py, gateway/run.py, and
# cli.py) or `HERMES_REDACT_SECRETS=false` in ~/.hermes/.env. An opt-out
# warning is logged at gateway and CLI startup so operators see the
# downgrade — see `_log_redaction_status()` in gateway/run.py and cli.py.
_REDACT_ENABLED = os.getenv("HERMES_REDACT_SECRETS", "true").lower() in {"1", "true", "yes", "on"}

# Known API key prefixes -- match the prefix + contiguous token chars
_PREFIX_PATTERNS = [
    # OpenAI / OpenRouter / Anthropic (sk-ant-*). Some provider-issued ``sk-``
    # keys carry dot-delimited body segments (Alibaba ``sk-sp-…``/``sk-ws-…``).
    # Each unit is one body char optionally preceded by a single dot, so the
    # body ends on its last non-dot char (sentence punctuation is never
    # consumed) and can never span ``..``: the ``sk-pro...EFGH`` display mask is
    # left alone by a second redaction pass instead of collapsing to ``***``.
    # No nested unbounded repeat: the dot is optional and followed by exactly
    # one body char, so every position has one parse (linear).
    r"sk-[A-Za-z0-9_-](?:\.?[A-Za-z0-9_-]){9,}",
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth access token
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub user-to-server token
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub server-to-server token
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub refresh token
    r"xapp-\d+-[A-Za-z0-9-]{10,}",      # Slack app-Level token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack bot/app/user tokens
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe secret key (live)
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe secret key (test)
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",          # SendGrid API key
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace token
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",            # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",           # AgentMail API key
    r"sk_[A-Za-z0-9_]{10,}",            # ElevenLabs TTS key (sk_ underscore, not sk- dash)
    r"tvly-[A-Za-z0-9]{10,}",           # Tavily search API key
    r"exa_[A-Za-z0-9]{10,}",            # Exa search API key
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API key
    r"syt_[A-Za-z0-9]{10,}",            # Matrix access token
    r"retaindb_[A-Za-z0-9]{10,}",       # RetainDB API key
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API key
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API key
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API key
    r"xai-[A-Za-z0-9]{30,}",            # xAI (Grok) API key
    r"ntn_[A-Za-z0-9]{10,}",            # Notion internal integration token
    r"fw_[A-Za-z0-9]{30,}",             # Fireworks AI API key
    # GitLab token families (each pattern keeps a full literal prefix so the
    # _PREFIX_SUBSTRINGS pre-screen stays false-negative-free). Ported from
    # openclaw/openclaw#112954; follow-up invited in #4541.
    r"glpat-[A-Za-z0-9_\-]{10,}",       # GitLab personal access token
    r"gloas-[A-Za-z0-9_\-]{10,}",       # GitLab OAuth application secret
    r"gldt-[A-Za-z0-9_\-]{10,}",        # GitLab deploy token
    r"glrt-[A-Za-z0-9_.\-]{10,}",       # GitLab runner authentication token (routable tokens are dotted)
    r"glrtr-[A-Za-z0-9_.\-]{10,}",      # GitLab runner registration token (routable)
    r"glcbt-[A-Za-z0-9_\-]{10,}",       # GitLab CI/CD job token
    r"glptt-[A-Za-z0-9_\-]{10,}",       # GitLab pipeline trigger token
    r"glft-[A-Za-z0-9_\-]{10,}",        # GitLab feed token
    r"glimt-[A-Za-z0-9_\-]{10,}",       # GitLab incoming mail token
    r"glagent-[A-Za-z0-9_\-]{10,}",     # GitLab agent (KAS) token
    r"glsoat-[A-Za-z0-9_\-]{10,}",      # GitLab service-account access token
    r"glffct-[A-Za-z0-9_\-]{10,}",      # GitLab feature-flags client token
    r"glwt-[A-Za-z0-9_\-]{10,}",        # GitLab workspace token
    r"GR1348941[A-Za-z0-9_\-]{10,}",    # GitLab legacy runner registration token
]

# ENV assignment patterns: KEY=value where KEY contains a secret-like name.
# Uppercase keys tolerate spaces around "=" (e.g. ``FOO_SECRET = bar``) because
# an all-caps key is almost never prose/code.
# Bare ``KEY`` / ``PASS`` / ``PW`` suffixes are included (``MCP_ACCESS_KEY=…``,
# ``FAL_KEY=…``, ``MYSQL_PASS=…``, ``DB_PW=…``). Those three are short enough
# to sit inside ordinary words (``KEYBOARD``, ``PASSAGE``, ``PWD``), so a match
# that rests on one of them alone must be word-bounded — see
# _key_has_secret_keyword. The legacy names keep embedded matching
# (``MYTOKEN=…``).
_SECRET_ENV_NAMES = r"(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PW|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
)

# Lowercase / dotted / hyphenated config keys from config files
# (application.properties, .env, YAML-ish dumps): ``spring.datasource.password=secret``,
# ``app.api.key=xyz``, ``password=secret``. The uppercase _ENV_ASSIGN_RE above
# never matched these, so config-file passwords leaked verbatim (issue #16413).
#
# These run only in a config-file context, NOT in prose, code, or URLs — three
# carve-outs preserved from the original design (#4367 + the documented
# web-URL passthrough below):
#   1. The value is bounded by ``[^\s&]`` (stops at whitespace AND ``&``) so
#      form-urlencoded bodies are handled pair-by-pair (by _redact_form_body),
#      not greedily swallowed.
#   2. _CFG_DOTTED_RE only matches when the key is NAMESPACED (contains a dot),
#      which is unambiguously a config key — never a prose word.
#   3. _CFG_ANCHORED_RE matches a bare secret-word key only at line start
#      (optionally after ``export``), so conversational ``I have password=foo``
#      mid-sentence is left alone.
# The colon-form URL guard (skip when ``://`` present) lives at the call site.
_SECRET_CFG_NAMES = r"(?:api[ _.\-]?key|token|secret|passwd|password|credential|auth)"
# Rendered line-number prefix: ``5|line`` (read_file), ``6:line`` (grep -n),
# ``7-line`` (grep -A/-B/-C context lines) and ``     8\tline`` (cat -n / nl:
# right-aligned number + TAB). Callers put the ONLY leading ``[ \t]*`` in front
# of it — stacking a second whitespace run around an optional gutter made the
# anchored passes quadratic on long indented lines (upstream 979576d938).
_LINE_NUMBER_GUTTER = r"(?:[0-9]+(?:[|:\-]|\t)[ \t]*)?"
_CFG_VALUE = r"(['\"]?)([^\s&]+?)\2(?=[\s&]|$)"
# Linear pre-gate for the _CFG_*_RE subs below: a text with no secret keyword
# can never match either pattern, so the (potentially backtrack-heavy) subs
# are skipped entirely for such text. See the call site in
# redact_sensitive_text().
_CFG_SECRET_WORD_RE = re.compile(_SECRET_CFG_NAMES, re.IGNORECASE)
# Namespaced (dotted) key: the secret word may sit anywhere in a dotted path.
# NOTE(perf): possessive quantifiers (py3.11+) replace the nested quantifier
# ``(?:[A-Za-z0-9_\-]+\.)+`` (exponential backtracking on long dotted runs).
# The ``*`` runs bordering {_SECRET_CFG_NAMES} must stay backtrackable
# (secret words are matchable by the class, e.g. ``app.api.key=…``).
# The lookbehind anchors each attempt to the start of a key run: without it,
# ``re.sub`` retries the backtrackable ``*`` prefix at every byte of a long
# non-matching dotted run, making the sub quadratic whenever the text contains
# a secret keyword anywhere (the ``_CFG_SECRET_WORD_RE`` pre-gate only skips
# secret-free text). Match set is unchanged — any match starting mid-run
# implies a leftmost match starting at the run start. The leading ``\.*+``
# keeps that true for runs that open with a dot (``.app.password=…``): the
# pre-lookbehind pattern matched those from the first segment, and without it
# the anchored form would skip them.
_CFG_DOTTED_RE = re.compile(
    rf"(?<![A-Za-z0-9_.\-])"
    rf"(\.*+[A-Za-z0-9_\-]++\.[A-Za-z0-9_.\-]*{_SECRET_CFG_NAMES}[A-Za-z0-9_.\-]*+"
    rf"|[A-Za-z0-9_.\-]*{_SECRET_CFG_NAMES}[A-Za-z0-9_.\-]*\.[A-Za-z0-9_.\-]++)"
    rf"={_CFG_VALUE}",
    re.IGNORECASE,
)
# Line-anchored bare key: ``password=…`` / ``export api_key=…`` at start of line.
# ``{_LINE_NUMBER_GUTTER}``: line-numbered dumps put the key behind a rendered
# gutter (``5|password=…`` from read_file, ``6:password=…`` from grep -n).
# Anchored at ``^`` without it, the rendered read of a secret-bearing file
# leaked what the raw text masked.
_CFG_ANCHORED_RE = re.compile(
    rf"(^[ \t]*{_LINE_NUMBER_GUTTER}(?:export[ \t]+)?[A-Za-z0-9_\-]*{_SECRET_CFG_NAMES}[A-Za-z0-9_\-]*)={_CFG_VALUE}",
    re.IGNORECASE | re.MULTILINE,
)

# Unquoted YAML / colon config (e.g. ``password: secret``,
# ``spring.datasource.password: hunter2``). The secret keyword must be part of
# the KEY (anchored to the start of the line/indent), and the value is a single
# whitespace-free token — so prose like ``note: secret meeting`` (keyword in the
# value) and ``error: token expired`` are left alone. Bare ``auth`` is excluded
# from the key set so ``Authorization:`` / ``author:`` don't match (the former
# is masked by _AUTH_HEADER_RE); ``auth_token``/``auth-token`` still match via
# the ``token`` keyword. Quoted values defer to _JSON_FIELD_RE via the lookahead.
_YAML_CFG_NAMES = r"(?:api[ _.\-]?key|token|secret|passwd|password|credential)"
# NOTE(perf): possessive quantifiers wherever the successor is disjoint; the
# leading ``[A-Za-z0-9_.\-]*`` stays backtrackable (see _CFG_DOTTED_RE note).
_YAML_ASSIGN_RE = re.compile(
    rf"(^[ \t]*+{_LINE_NUMBER_GUTTER}[A-Za-z0-9_.\-]*{_YAML_CFG_NAMES}[A-Za-z0-9_.\-]*+)(:[ \t]*+)(?!['\"])([^\s&]++)",
    re.IGNORECASE | re.MULTILINE,
)
# Quoted YAML scalar (``api_key: "value"`` / ``password: 'value'``). The
# unquoted rule above defers quoted values to _JSON_FIELD_RE, which only
# handles a quoted KEY, so a YAML line with a quoted value was never masked.
# Only run for secret-bearing files (``secret_file=True``): elsewhere a quoted
# value next to a keyword is as likely to be code or prose.
_YAML_QUOTED_ASSIGN_RE = re.compile(
    rf"(^[ \t]*+{_LINE_NUMBER_GUTTER}[A-Za-z0-9_.\-]*{_YAML_CFG_NAMES}[A-Za-z0-9_.\-]*+)(:[ \t]*+)(['\"])([^'\"\n]+)\3",
    re.IGNORECASE | re.MULTILINE,
)

# Values in a secret-bearing file that are configuration, not credentials:
# YAML booleans/null and short numbers (``max_tokens: 4096``,
# ``redact_secrets: true``). Masking them buys nothing and hides settings the
# agent legitimately edits. Numbers under a password-class key stay masked.
_NON_SECRET_SCALARS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "none", "~"})
_SHORT_NUMBER_RE = re.compile(r"-?[0-9]{1,11}(?:\.[0-9]+)?")
_PASSWORD_KEY_RE = re.compile(r"passwd|password|pass|pw", re.IGNORECASE)


def _is_config_scalar(key: str, value: str) -> bool:
    """True for a secret-file value that is plainly a setting (see above)."""
    v = value.strip().lower()
    if v in _NON_SECRET_SCALARS:
        return True
    return bool(_SHORT_NUMBER_RE.fullmatch(v)) and not _PASSWORD_KEY_RE.search(key)

# Word-boundary validation for the assignment passes above.
#
# The key classes allow arbitrary affixes around the secret keyword so real key
# names (``client_secret``, ``clientSecret``, ``s3.secret-key``, ``dbpassword``)
# match. The side effect: ordinary document words that merely CONTAIN a keyword
# matched too — ``Secretary: J.Smith``, ``tokenizer: cl100k_base``,
# ``author=Smith`` — and had their values mangled (ported from upstream,
# nearai/ironclaw#6129).
#
# Mixed/lowercase keys: a keyword occurrence counts only when it ENDS a word —
# at the key's edge, before a non-letter (``_ - . 3``), at a camelCase
# transition (``secretKey``) or before a plural ``s`` (``secrets:``). Unlike
# upstream, the START of the keyword is not checked, so concatenated compounds
# (``dbpassword``, ``clientsecret``, ``mytoken``) stay masked; only the
# keyword-as-word-prefix prose shape (``secretary``, ``tokenizer``,
# ``authored``, ``credentialing``, ``passwordless``) is let through.
#
# ALL-CAPS keys (the _ENV_ASSIGN_RE shape) keep legacy embedded matching for
# the long names (``MYTOKEN=…``) — an all-caps key is almost never prose. The
# short bare ``KEY``/``PASS``/``PW`` names must be word-bounded on BOTH sides,
# so ``MCP_ACCESS_KEY`` / ``DB_PW`` match and ``KEYBOARD`` / ``PASSAGE`` /
# ``PWD`` / ``OLDPWD`` / ``BYPASS`` do not.
_KEY_KEYWORD_RE = re.compile(
    r"(?:api|auth|access|refresh|session|secret)[ _.\-]?(?:key|token)"
    r"|token|secret|passwd|password|credential|auth",
    re.IGNORECASE,
)
_UPPER_LEGACY_KEYWORD_RE = re.compile(r"API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH")
_UPPER_BARE_KEYWORD_RE = re.compile(r"KEY|PASS|PW")


def _is_word_start(s: str, i: int) -> bool:
    """True if position ``i`` in ``s`` begins a word (not mid-word)."""
    if i == 0:
        return True
    prev, cur = s[i - 1], s[i]
    if not prev.isalpha():
        return True
    if cur.isupper() and prev.islower():
        return True  # camelCase: clientSecret
    # Acronym run ending: APIToken — the 'T' begins a new word when it is
    # followed by lowercase while the preceding run is uppercase.
    if cur.isupper() and prev.isupper() and i + 1 < len(s) and s[i + 1].islower():
        return True
    return False


def _is_word_end(s: str, j: int, *, allow_plural: bool = True) -> bool:
    """True if position ``j`` (exclusive end) in ``s`` ends a word."""
    if j >= len(s):
        return True
    cur = s[j]
    if not cur.isalpha():
        return True
    if cur.isupper() and s[j - 1].islower():
        return True  # camelCase continuation: secretKey
    if allow_plural and cur in "sS":
        return _is_word_end(s, j + 1, allow_plural=False)
    return False


def _key_has_secret_keyword(key: str) -> bool:
    """Post-match validator for the ENV / config / YAML assignment passes.

    Rejects keys whose only keyword is embedded in a larger word (see the
    comment above _KEY_KEYWORD_RE for the exact rule per key shape).
    """
    letters = [c for c in key if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        if _UPPER_LEGACY_KEYWORD_RE.search(key):
            return True
        return any(
            _is_word_start(key, m.start()) and _is_word_end(key, m.end())
            for m in _UPPER_BARE_KEYWORD_RE.finditer(key)
        )
    return any(_is_word_end(key, m.end()) for m in _KEY_KEYWORD_RE.finditer(key))


# JSON field patterns: "apiKey": "value", "token": "value", etc.
# ``x-<name>-key`` custom API-key header names (``x-brain-key`` — the Open Brain
# MCP header in config.yaml — ``x-functions-key``, ``x-api-key``). Each
# dash-separated segment is whole, so ``x-monkey`` / ``x-keyboard`` /
# ``inbox-key`` / prose ``*-key`` words do not match; the lookbehind keeps it
# from starting mid-word. Used by the header, JSON and Python-repr rules.
_X_KEY_HEADER_NAME = r"(?<![A-Za-z0-9_\-])x-(?:[a-z0-9]+-)*key"
_X_KEY_HEADER_NAME_RE = re.compile(_X_KEY_HEADER_NAME, re.IGNORECASE)
_JSON_KEY_NAMES = rf"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material|{_X_KEY_HEADER_NAME})"
_JSON_FIELD_RE = re.compile(
    rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# Python ``repr`` uses single-quoted mapping fields, so opaque credentials in
# tracebacks, logged kwargs and pytest failure introspection bypass the
# double-quoted JSON rule above: ``{'BRAVE_API_KEY': 'opaque-value'}``,
# ``headers={'Authorization': 'Bearer …'}`` (the ``Authorization:`` header rule
# never sees the quote-split form). Capture identifier-shaped keys here, then
# apply the key policy in the callback.
_PYTHON_REPR_SECRET_KEYS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "secret",
    "password",
    "passwd",
    "private_key",
    "credential",
    "credentials",
    "authorization",
    "proxy-authorization",
    "bearer",
    "secret_value",
    "raw_secret",
    "secret_input",
    "key_material",
})
_PYTHON_REPR_ENV_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_PASSWD",
    "_CREDENTIAL",
    "_CREDENTIALS",
)
# Casefolded credential suffixes for mixed/camel-case key names
# (``UserPassword``, ``sessionToken``, ``clientApiKey``). Suffix-only so
# ``token_count`` / ``password_policy`` metadata keys never match.
_PYTHON_REPR_CREDENTIAL_SUFFIXES = (
    "apikey",
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "credentials",
)
_PYTHON_REPR_FIELD_RE = re.compile(
    r"'(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)'(?P<sep>\s*:\s*)"
    r"(?:"
    r"(?P<single_prefix>[bB]?)'(?P<single_value>(?:\\.|[^'\\])+)'"
    r"|(?P<double_prefix>[bB]?)\"(?P<double_value>(?:\\.|[^\"\\])+)\""
    r")"
)
# Programmatic env lookups (``os.getenv(...)``, ``process.env.X``) as a repr
# VALUE name a variable; they are not a leaked secret.
_ENV_LOOKUP_VALUE_RE = re.compile(r"^(?:os\.(?:getenv|environ)|process\.env|\$ENV\{)")

# Terminal/process output normally uses ``code_file=True`` to preserve source.
# Add repr masking only to high-confidence diagnostic lines: pytest assertion
# introspection (``E       ...``) and final Python exception lines.
_PYTEST_DIAGNOSTIC_LINE_RE = re.compile(r"^(?P<prefix>[ \t]*E[ \t]{2,})(?P<body>.*)$")
_PYTHON_EXCEPTION_LINE_RE = re.compile(
    r"^(?P<prefix>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*"
    r"(?:Error|Exception|Warning):[ \t]*)(?P<body>.*)$"
)

# Authorization headers — any scheme (Bearer, Basic, Token, Digest, …) plus the
# bare-credential form, and Proxy-Authorization. The credential token is masked
# while the header name and scheme word are preserved for debuggability. The
# previous rule only matched ``Bearer``, so ``Basic <base64 user:pass>`` and
# ``token <pat>`` leaked verbatim into logs/transcripts.
#
# The credential class excludes quote characters (``"`` / ``'``): a token sitting
# flush against a closing quote (``"Authorization: Bearer sk-..."``) must not pull
# that quote into the match, or masking turns value corruption into *syntax*
# corruption — the closing quote vanishes and the command/string no longer parses
# (unterminated quote → shell EOF / Python SyntaxError). Real credentials never
# contain ``"`` or ``'``, so excluding them is safe. See #43083.
_AUTH_HEADER_RE = re.compile(
    r"((?:Proxy-)?Authorization:\s*)([A-Za-z][\w.+-]*\s+)?([^\s\"']+)",
    re.IGNORECASE,
)

# API-key style auth headers carrying a single opaque value (no scheme word).
# Anthropic and many providers authenticate with ``x-api-key``; values without
# a known vendor prefix (custom/local backends) would otherwise leak when a
# request or curl command is logged or echoed into tool output / transcripts.
_SECRET_HEADER_NAMES = (
    r"(?:x-api-key|x-goog-api-key|api-key|apikey|x-api-token|x-auth-token|x-access-token"
    rf"|{_X_KEY_HEADER_NAME})"
)
_SECRET_HEADER_RE = re.compile(
    rf"({_SECRET_HEADER_NAMES}\s*:\s*)(\S+)",
    re.IGNORECASE,
)

# Telegram bot tokens: bot<digits>:<token> or <digits>:<token>,
# where token part is restricted to [-A-Za-z0-9_] and length >= 30
_TELEGRAM_RE = re.compile(
    r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})",
)

# Private key blocks: -----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# Database connection strings: protocol://user:PASSWORD@host
# Catches postgres, mysql, mongodb, redis, amqp URLs and redacts the password.
# The userinfo and password groups forbid whitespace ([^:\s]+ / [^@\s]+) so the
# match can never span a line break. A real DSN password never contains
# whitespace; without this bound the greedy [^@]+ would scan past the end of a
# code line to the next stray "@" (e.g. a Python decorator), swallowing
# intervening lines and corrupting tool OUTPUT for any source containing a
# postgresql:// f-string template. See issue #33801.
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s]+:)([^@\s]+)(@)",
    re.IGNORECASE,
)

# Bare-token credential in a web/transport URL: ``scheme://TOKEN@host``.
# This is the ``git remote set-url origin https://PASSWORD@github.com/...``
# shape from issue #6396 — a single opaque credential in the userinfo position
# with NO ``user:pass`` colon. It is unambiguously a secret: legitimate
# round-trip URLs (OAuth callbacks, magic links, pre-signed shares — see the
# "Web-URL redaction is intentionally OFF" note in redact_sensitive_text) carry
# their tokens in the QUERY STRING, never in bare userinfo. The colon form
# ``user:pass@`` is deliberately left to pass through (commit "pass web URLs
# through unchanged", #34029) and is NOT matched here — the token class forbids
# ``:``. DB schemes are handled by _DB_CONNSTR_RE above and excluded here.
#
# Guards against false positives:
#   - 8+ char floor skips short usernames (git, admin, root, deploy, ubuntu).
#   - The token class ``[^\s:@/]`` cannot cross ``/``, so an ``@`` sitting in a
#     path or query (e.g. ``?q=user@example.com``) is never treated as userinfo.
_URL_BARE_TOKEN_RE = re.compile(
    r"((?:https?|wss?|git|ssh|ftp|ftps|sftp)://)"  # scheme
    r"([^\s:@/]{8,})"                               # bare token (no colon/slash/@), 8+ chars
    r"(@[^\s]+)",                                   # @host...
    re.IGNORECASE,
)

# JWT tokens: header.payload[.signature] — always start with "eyJ" (base64 for "{")
# Matches 1-part (header only), 2-part (header.payload), and full 3-part JWTs.
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}"           # Header (always starts with eyJ)
    r"(?:\.[A-Za-z0-9_=-]{4,}){0,2}"   # Optional payload and/or signature
)

# E.164 phone numbers: +<country><number>, 7-15 digits
# Negative lookahead prevents matching hex strings or identifiers
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# URLs containing query strings — matches `scheme://...?...[# or end]`.
# Used to scan text for URLs whose query params may contain secrets.
# Ported from nearai/ironclaw#2529.
_URL_WITH_QUERY_RE = re.compile(
    r"(https?|wss?|ftp)://"          # scheme
    r"([^\s/?#]+)"                    # authority (may include userinfo)
    r"([^\s?#]*)"                     # path
    r"\?([^\s#]+)"                    # query (required)
    r"(#\S*)?",                       # optional fragment
)

# URLs containing userinfo — `scheme://user:password@host` for ANY scheme
# (not just DB protocols already covered by _DB_CONNSTR_RE above).
# Catches things like `https://user:token@api.example.com/v1/foo`.
_URL_USERINFO_RE = re.compile(
    r"(https?|wss?|ftp)://([^/\s:@]+):([^/\s@]+)@",
)

# HTTP access logs often use a relative request target rather than a full URL:
# `"POST /webhook?password=... HTTP/1.1"`. The full-URL redactor above only
# sees strings containing `://`, so handle request-target query strings too.
_HTTP_REQUEST_TARGET_QUERY_RE = re.compile(
    r"\b((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT)\s+[^ \t\r\n\"']*?)"
    r"\?([^ \t\r\n\"']+)",
    re.IGNORECASE,
)

# Form-urlencoded body detection: conservative — only applies when the entire
# text looks like a query string (k=v&k=v pattern with no newlines).
_FORM_BODY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*(?:&[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*)+$"
)

# Control / zero-width characters that can split a token body: a secret
# emitted as ``sk-abc\x1bdef…`` or wrapped as ``ghp_abc\n123…`` escapes the
# contiguous prefix regexes (upstream #77484). Used by
# _mask_control_split_tokens. A single character class, so it is linear.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202f\u2060\ufeff]"
)

# Union of every _PREFIX_PATTERNS body class. A control-stripped match may only
# span original chars that are token-body or control chars. ``=`` is excluded on
# purpose: a KEY=value separator must never let a match span unrelated text.
_TOKEN_BODY_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-."
)

# Compile known prefix patterns into one alternation
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)

# Zhipu API keys use an unprefixed ``id.secret`` form. Deliberately
# provider-shaped rather than a generic high-entropy dotted-token rule: the id
# is exactly 32 lowercase hex chars and the secret a run of at least 16
# alphanumerics, so content-hash filenames (``<sha>.bundle``, ``<md5>.sqlite3``)
# never match. A trailing dot is allowed only when it ends the token (sentence
# punctuation), not when another segment follows (local: upstream leaves a
# sentence-final key in cleartext). Fixed-width id + one unbounded run: linear.
_ZHIPU_API_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([0-9a-f]{32}\.[A-Za-z0-9]{16,})(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])"
)


def _mask_control_split_tokens(text: str, mask_fn) -> str:
    """Mask tokens whose body is split by control/zero-width characters.

    A credential like ``sk-abc\\x1bdef456…`` or ``ghp_abc\\n123def…`` has its
    body interrupted, so the contiguous _PREFIX_RE cannot match it and the
    secret leaks verbatim (upstream #77484). Match on a control-stripped copy
    (where the token is contiguous again, even when each fragment alone is too
    short), then mask the corresponding span in the ORIGINAL, but only when
    that span holds solely token-body and control chars, so a match can never
    cross into another line's unrelated text (``EXA_API_KEY=…``).

    Linear: one strip, one index map, one _PREFIX_RE scan of the stripped copy,
    and per-match work bounded by the match span (matches do not overlap).
    """
    ctrl = [m.start() for m in _CONTROL_CHARS_RE.finditer(text)]
    if not ctrl:
        return text
    stripped = _CONTROL_CHARS_RE.sub("", text)
    # Control i sits before ``ctrl[i] - i`` stripped chars, so stripped index j
    # maps back to ``j + (controls at or before j)`` in the original.
    ctrl_at = [pos - i for i, pos in enumerate(ctrl)]

    def orig(j: int) -> int:
        return j + bisect.bisect_right(ctrl_at, j)

    matches = []
    for m in _PREFIX_RE.finditer(stripped):
        start_orig = orig(m.start(1))
        end_orig = orig(m.end(1) - 1) + 1
        span = text[start_orig:end_orig]
        # A span crossing a LINE boundary whose fragment already matches on its
        # own is left to the ordinary prefix pass: joining would swallow the
        # next line (``ghp_<tok>\nbutton [ref=e3]`` masked ``button``). Non-line
        # controls (ESC, ZWSP, …) never legitimately sit between a token and
        # prose, so there the join proceeds even when the head self-matches, or
        # the tail of ``sk-<head>\x1b<tail>`` would leak.
        if ("\n" in span or "\r" in span) and _PREFIX_RE.search(span):
            continue
        # Reject spans holding a non-token char, and matches running into a
        # ``KEY=`` name (a real value is followed by a newline/space/end).
        if (all(c in _TOKEN_BODY_CHARS or _CONTROL_CHARS_RE.match(c) for c in span)
                and (end_orig >= len(text) or text[end_orig] != "=")):
            matches.append((start_orig, end_orig, mask_fn(m.group(1))))
    if not matches:
        return text
    parts, last = [], 0
    for start_orig, end_orig, replacement in matches:
        parts += (text[last:start_orig], replacement)
        last = end_orig
    parts.append(text[last:])
    return "".join(parts)


def mask_secret(
    value: str,
    *,
    head: int = 4,
    tail: int = 4,
    floor: int = 12,
    placeholder: str = "***",
    empty: str = "",
) -> str:
    """Mask a secret for display, preserving ``head`` and ``tail`` characters.

    Canonical helper for display-time redaction across Hermes — used by
    ``hermes config``, ``hermes status``, ``hermes dump``, and anywhere
    a secret needs to be shown truncated for debuggability while still
    keeping the bulk hidden.

    Args:
        value:       The secret to mask. ``None``/empty returns ``empty``.
        head:        Leading characters to preserve. Default 4.
        tail:        Trailing characters to preserve. Default 4.
        floor:       Values shorter than ``head + tail + floor_margin`` are
                     fully masked (returns ``placeholder``). Default 12 —
                     matches the existing config/status/dump convention.
        placeholder: Value returned for too-short inputs. Default ``"***"``.
        empty:       Value returned when ``value`` is falsy (None, ""). The
                     caller can override this to e.g. ``color("(not set)",
                     Colors.DIM)`` for user-facing display.

    Examples:
        >>> mask_secret("sk-proj-abcdef1234567890")
        'sk-p...7890'
        >>> mask_secret("short")                         # fully masked
        '***'
        >>> mask_secret("")                              # empty default
        ''
        >>> mask_secret("", empty="(not set)")           # empty override
        '(not set)'
        >>> mask_secret("long-token", head=6, tail=4, floor=18)
        '***'
    """
    if not value:
        return empty
    if len(value) < floor:
        return placeholder
    return f"{value[:head]}...{value[-tail:]}"


def _is_python_repr_secret_key(key: str) -> bool:
    """Return True for exact secret keys or credential-suffixed key names."""
    folded = key.casefold()
    if folded in _PYTHON_REPR_SECRET_KEYS:
        return True
    if key.isupper() and key.endswith(_PYTHON_REPR_ENV_SUFFIXES):
        return True
    # Header-dict keys: ``{'x-brain-key': '…'}``, ``{'X-Api-Key': '…'}``.
    if _X_KEY_HEADER_NAME_RE.fullmatch(key):
        return True
    # Mixed/camel-case keys ending in a credential word (``UserPassword``,
    # ``sessionToken``, ``clientApiKey``). Suffix-only matching keeps metadata
    # names like ``TOKEN_COUNT`` / ``PASSWORD_POLICY`` / ``SECRET_NAME`` intact.
    return folded.endswith(_PYTHON_REPR_CREDENTIAL_SUFFIXES)


def _redact_python_repr_fields(text: str) -> str:
    """Fully mask credential fields in Python mapping ``repr`` output."""
    def _sub(match: re.Match) -> str:
        key = match.group("key")
        if not _is_python_repr_secret_key(key):
            return match.group(0)

        single_value = match.group("single_value")
        if single_value is not None:
            prefix = match.group("single_prefix") or ""
            quote = "'"
            value = single_value
        else:
            prefix = match.group("double_prefix") or ""
            quote = '"'
            value = match.group("double_value")

        # Mapping repr can contain code-shaped fixture values too. Preserve
        # programmatic env lookups.
        if _ENV_LOOKUP_VALUE_RE.match(value):
            return match.group(0)
        # An earlier pass already masked this value; re-masking would erase
        # what it deliberately kept (``'Authorization': 'Digest ***'`` → ``'***'``,
        # or the vendor label of ``«redacted:ghp_…»``).
        if "***" in value or value.startswith("«redacted"):
            return match.group(0)
        # Do not retain head/tail characters here: escaped repr atoms can cross
        # a slicing boundary and leave an unescaped quote behind. A full mask is
        # parseable for both str and bytes values and leaks no opaque bytes.
        return f"'{key}'{match.group('sep')}{prefix}{quote}***{quote}"

    return _PYTHON_REPR_FIELD_RE.sub(_sub, text)


def _redact_python_diagnostic_repr_fields(text: str) -> str:
    """Mask repr fields only on pytest/error lines in source-preserving output."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        ending = ""
        body_line = line
        if line.endswith("\r\n"):
            body_line, ending = line[:-2], "\r\n"
        elif line.endswith("\n") or line.endswith("\r"):
            body_line, ending = line[:-1], line[-1:]

        match = _PYTEST_DIAGNOSTIC_LINE_RE.match(body_line)
        if match is None:
            match = _PYTHON_EXCEPTION_LINE_RE.match(body_line)
        if match is not None:
            lines[index] = (
                match.group("prefix")
                + _redact_python_repr_fields(match.group("body"))
                + ending
            )
    return "".join(lines)


def _mask_token(token: str) -> str:
    """Mask a log token — conservative 18-char floor, preserves 6 prefix / 4 suffix."""
    # Empty input: historically this returned "***" rather than "". Preserve.
    if not token:
        return "***"
    return mask_secret(token, head=6, tail=4, floor=18)


def _redact_query_string(query: str) -> str:
    """Redact sensitive parameter values in a URL query string.

    Handles `k=v&k=v` format. Sensitive keys (case-insensitive) have values
    replaced with `***`. Non-sensitive keys pass through unchanged.
    Empty or malformed pairs are preserved as-is.
    """
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        if key.lower() in _SENSITIVE_QUERY_PARAMS:
            parts.append(f"{key}=***")
        else:
            parts.append(pair)
    return "&".join(parts)


def _redact_url_query_params(text: str) -> str:
    """Scan text for URLs with query strings and redact sensitive params.

    Catches opaque tokens that don't match vendor prefix regexes, e.g.
    `https://example.com/cb?code=ABC123&state=xyz` → `...?code=***&state=xyz`.
    """
    def _sub(m: re.Match) -> str:
        scheme = m.group(1)
        authority = m.group(2)
        path = m.group(3)
        query = _redact_query_string(m.group(4))
        fragment = m.group(5) or ""
        return f"{scheme}://{authority}{path}?{query}{fragment}"
    return _URL_WITH_QUERY_RE.sub(_sub, text)


def _redact_url_userinfo(text: str) -> str:
    """Strip `user:password@` from HTTP/WS/FTP URLs.

    DB protocols (postgres, mysql, mongodb, redis, amqp) are handled
    separately by `_DB_CONNSTR_RE`.
    """
    return _URL_USERINFO_RE.sub(
        lambda m: f"{m.group(1)}://{m.group(2)}:***@",
        text,
    )


def redact_cdp_url(value: object) -> str:
    """Mask secrets in a CDP/browser endpoint URL before it is logged.

    The global ``redact_sensitive_text`` deliberately passes web-URL query
    params and ``user:pass@`` userinfo through unmasked (OAuth callbacks,
    magic-link / pre-signed URLs the agent is meant to follow -- see the
    web-URL note above). CDP discovery endpoints are NOT such a workflow:
    their query-string tokens and userinfo passwords are pure credentials
    that must never reach the logs. So for CDP URLs we opt INTO the two URL
    redactors that the global pass leaves off.

    This is the single source of truth for redacting a CDP URL that is passed
    *directly* to a log or error message. Callers that instead need to redact an
    exception whose text embeds the URL (e.g. a ``websockets`` connect error)
    should route that through their own error-text helper, which delegates here
    -- see ``tools.browser_supervisor._redact_cdp_error_text``.
    """
    text = redact_sensitive_text("" if value is None else str(value))
    if not text:
        return text
    text = _redact_url_query_params(text)
    text = _redact_url_userinfo(text)
    return text


def _redact_http_request_target_query_params(text: str) -> str:
    """Redact sensitive query params in HTTP access-log request targets."""
    def _sub(m: re.Match) -> str:
        prefix = m.group(1)
        query = _redact_query_string(m.group(2))
        return f"{prefix}?{query}"
    return _HTTP_REQUEST_TARGET_QUERY_RE.sub(_sub, text)


def _redact_form_body(text: str) -> str:
    """Redact sensitive values in a form-urlencoded body.

    Only applies when the entire input looks like a pure form body
    (k=v&k=v with no newlines, no other text). Single-line non-form
    text passes through unchanged. This is a conservative pass — the
    `_redact_url_query_params` function handles embedded query strings.
    """
    if not text or "\n" in text or "&" not in text:
        return text
    # The body-body form check is strict: only trigger on clean k=v&k=v.
    if not _FORM_BODY_RE.match(text.strip()):
        return text
    return _redact_query_string(text.strip())


def _mask_token_nonreusable(token: str) -> str:
    """Redact a prefix-matched credential to a NON-REUSABLE sentinel.

    Unlike :func:`_mask_token` (which keeps head/tail chars — fine for logs
    that are never fed back into a config), this emits a marker that:

    * cannot be mistaken for a usable-but-truncated key, so an agent that
      reads it from a config file and writes it back does NOT corrupt the
      stored credential into a dead 13-char string (issue #35519); and
    * still does not leak the secret material (no head/tail chars).

    The vendor prefix label is preserved for debuggability so the agent can
    still tell *which* credential is present (e.g. a GitHub PAT vs an OpenAI
    key) without seeing any of its bytes.
    """
    if not token:
        return "«redacted-secret»"
    # Preserve only the recognizable vendor prefix label (e.g. "ghp_", "sk-"),
    # never any of the random secret body.
    label = ""
    for sub in _PREFIX_SUBSTRINGS:
        if token.startswith(sub):
            label = sub
            break
    return f"«redacted:{label}…»" if label else "«redacted-secret»"


def redact_sensitive_text(
    text: str,
    *,
    force: bool = False,
    code_file: bool = False,
    file_read: bool = False,
    secret_file: bool = False,
) -> str:
    """Apply all redaction patterns to a block of text.

    Safe to call on any string -- non-matching text passes through unchanged.
    Enabled by default. Disable via security.redact_secrets: false in config.yaml.
    Set force=True for safety boundaries that must never return raw secrets
    regardless of the user's global logging redaction preference.

    Set code_file=True to skip the ENV-assignment and JSON-field regex
    patterns when the text is known to be source code (e.g. MAX_TOKENS=***
    constants, "apiKey": "test" fixtures). Prefix patterns, auth headers,
    private keys, DB connstrings, JWTs, and URL secrets are still redacted.

    Set file_read=True for file *content* returned to the agent (read_file /
    search_files / cat). Secrets are STILL redacted — they are never exposed —
    but prefix-matched credentials are replaced with a non-reusable sentinel
    (``«redacted:ghp_…»``) instead of a head/tail-preserving mask
    (``ghp_S1...Pn2T``). The old mask looked like a real-but-truncated key, so
    an agent reading it from config.yaml and writing it back silently corrupted
    the stored credential into a dead 13-char value → 401 (issue #35519). The
    sentinel is syntactically invalid as a token, so it can't be mistaken for a
    usable key or written back as one. Implies code_file=True (config/data
    files shouldn't trigger the source-code ENV/JSON false-positive paths)
    unless ``secret_file`` is set.

    Set ``secret_file=True`` when the caller has classified the SOURCE as
    secret-bearing (``is_secret_file_path``: Hermes ``config.yaml`` and its
    backups, ``.env``-style files, shell rc files). It re-enables the
    ENV/JSON/YAML assignment passes that ``file_read`` / ``code_file`` would
    skip, so an opaque prefix-less credential under a credential-shaped key
    (``  api_key: <v>``) is masked instead of returned in cleartext. It is
    authoritative over ``code_file``, so a caller cannot be fail-open by
    setting both. With ``file_read=True`` those assignments get the
    non-reusable sentinel (the #35519 write-back hazard stays closed), quoted
    YAML values are masked too, and plain settings (booleans, null, short
    numbers under non-password keys) are left readable.

    Performance: each regex pattern is gated behind a cheap substring
    pre-check (e.g. ``"=" in text`` for ENV assignments, ``"://" in text``
    for URLs, ``"eyJ" in text`` for JWTs). On a typical hermes log line
    (no secrets) this drops the 13-pattern scan from ~5.6us to ~1.8us per
    record (-68%). The pre-checks are conservative — false positives
    still run the full regex, which then doesn't match. False negatives
    are impossible because every regex requires the gated substring to
    match.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not (force or _REDACT_ENABLED):
        return text

    # file_read content shouldn't hit the source-code ENV/JSON false-positive
    # paths either (it's config/data, not log lines).
    if file_read:
        code_file = True
    # ``secret_file`` is authoritative: a caller that classified the source as
    # secret-bearing must not be fail-open because another flag was also set.
    if secret_file:
        code_file = False
    # File-read content gets the non-reusable sentinel on every pass, so an
    # agent cannot write a truncated-looking mask back as a credential.
    _assign_mask = _mask_token_nonreusable if file_read else _mask_token

    def _leave_value(key: str, value: str) -> bool:
        # An earlier pass already masked this value (``***`` or the
        # ``«redacted:…»`` sentinel); masking again only erases the vendor
        # label the sentinel deliberately keeps.
        if value == "***" or value.startswith("«redacted"):
            return True
        if not secret_file:
            return False
        # A URL value (``token_url: https://…``) is left to the URL rules:
        # web URLs pass through by design, DB connection-string passwords
        # are masked by _DB_CONNSTR_RE, bare userinfo by _URL_BARE_TOKEN_RE.
        return "://" in value or _is_config_scalar(key, value)

    # Known prefixes (sk-, ghp_, etc.) — gate on substring presence
    if _has_known_prefix_substring(text):
        _prefix_sub = _mask_token_nonreusable if file_read else _mask_token
        # Control/zero-width chars (\n, \r, ESC, U+200B, …) can split a token
        # body so _PREFIX_RE cannot match across them (upstream #77484).
        text = _mask_control_split_tokens(text, _prefix_sub)
        text = _PREFIX_RE.sub(lambda m: _prefix_sub(m.group(1)), text)

    # Prefix-less Zhipu ``id.secret`` keys (upstream 7b57cda6d9 / aebc71d78c).
    # Runs on every surface, code files included, like the prefix pass.
    if "." in text:
        _zhipu_sub = _mask_token_nonreusable if file_read else _mask_token
        text = _ZHIPU_API_KEY_RE.sub(lambda m: _zhipu_sub(m.group(1)), text)

    # ENV assignments: OPENAI_API_KEY=***  (skip for code files — false positives)
    if not code_file:
        if "=" in text:
            def _redact_env(m):
                name, quote, value = m.group(1), m.group(2), m.group(3)
                # Keyword must sit at a word boundary within the key —
                # ``KEYBOARD=…`` / ``author=Smith`` are not credentials.
                if not _key_has_secret_keyword(name) or _leave_value(name, value):
                    return m.group(0)
                return f"{name}={quote}{_assign_mask(value)}{quote}"
            text = _ENV_ASSIGN_RE.sub(_redact_env, text)
            # Lowercase/dotted config keys (issue #16413). Skip URLs entirely —
            # web-URL query params are intentionally passed through (see note
            # near the bottom of this function); _DB_CONNSTR_RE still guards
            # connection-string passwords.
            #
            # Extra gate: every _CFG_*_RE match requires a secret keyword in
            # the key, so a text without any secret keyword cannot match —
            # skipping is exact. This matters because _CFG_DOTTED_RE
            # backtracks on long unbroken [A-Za-z0-9_.\-] runs (e.g.
            # base64/hex blobs in compaction payloads); the linear keyword
            # scan prevents that pathological path on secret-free text.
            #
            # Secret-bearing files: the whole-text ``://`` skip would disable
            # the line-anchored pass for any config that mentions a URL
            # anywhere (every Hermes config.yaml has a ``base_url``). There,
            # the anchored pass runs regardless and only a URL-shaped VALUE is
            # left alone. The unanchored dotted pass keeps the skip.
            if _CFG_SECRET_WORD_RE.search(text):
                if "://" not in text:
                    text = _CFG_DOTTED_RE.sub(_redact_env, text)
                if secret_file or "://" not in text:
                    text = _CFG_ANCHORED_RE.sub(_redact_env, text)

        # JSON fields: "apiKey": "***"  (skip for code files — false positives)
        if ":" in text and '"' in text:
            def _redact_json(m):
                key, value = m.group(1), m.group(2)
                if _leave_value(key, value):
                    return m.group(0)
                return f'{key}: "{_assign_mask(value)}"'
            text = _JSON_FIELD_RE.sub(_redact_json, text)

        # Python mapping repr fields ({'API_KEY': '…'}): single-quoted, so the
        # JSON rule above never sees them — the traceback / logged-kwargs /
        # pytest-introspection leak shape.
        if ":" in text and "'" in text:
            text = _redact_python_repr_fields(text)

        # Unquoted YAML / colon config: password: ***  (after JSON so quoted
        # values are handled there; the lookahead in _YAML_ASSIGN_RE skips
        # quotes). Skip URLs — web-URL query params pass through by design.
        # Secret-bearing files run it even with a URL elsewhere in the text
        # (see the _CFG_ANCHORED_RE note above); URL-shaped values are kept.
        if ":" in text and (secret_file or "://" not in text):
            def _redact_yaml(m):
                key, sep, value = m.group(1), m.group(2), m.group(3)
                # ``Secretary: J.Smith`` / ``tokenizer: cl100k_base`` are
                # document text, not credentials.
                if not _key_has_secret_keyword(key) or _leave_value(key, value):
                    return m.group(0)
                return f"{key}{sep}{_assign_mask(value)}"
            text = _YAML_ASSIGN_RE.sub(_redact_yaml, text)
            if secret_file and ("'" in text or '"' in text):
                def _redact_yaml_quoted(m):
                    key, sep, quote, value = m.group(1), m.group(2), m.group(3), m.group(4)
                    if not _key_has_secret_keyword(key) or _leave_value(key, value):
                        return m.group(0)
                    return f"{key}{sep}{quote}{_assign_mask(value)}{quote}"
                text = _YAML_QUOTED_ASSIGN_RE.sub(_redact_yaml_quoted, text)

    # Authorization headers — _AUTH_HEADER_RE matches any scheme after
    # "[Proxy-]Authorization:" case-insensitively, so "uthorization" is the
    # cheapest substring gate that covers every casing without a casefold().
    if "uthorization" in text or "UTHORIZATION" in text:
        text = _AUTH_HEADER_RE.sub(
            lambda m: m.group(1) + (m.group(2) or "") + _mask_token(m.group(3)),
            text,
        )

    # API-key style headers (x-api-key, api-key, …). Header values are
    # colon-separated, so gate on ":" — the regex itself is the precise filter.
    if ":" in text:
        def _redact_secret_header(m):
            value = m.group(2)
            # Keep quotes outside the mask: a quoted YAML scalar keeps both,
            # and a header flush against a closing quote (``-H "x-api-key: v"``)
            # keeps that quote, so masking never breaks the command's syntax.
            lead = trail = ""
            if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
                lead, trail, value = value[0], value[-1], value[1:-1]
            elif len(value) >= 2 and value[-1] in "'\"":
                trail, value = value[-1], value[:-1]
            if value == "***" or value.startswith("«redacted"):
                return m.group(0)
            # File reads get the non-reusable sentinel, like every other pass:
            # a head/tail mask of ``x-brain-key: …`` in config.yaml looks like a
            # truncated real key and could be written back (#35519).
            return f"{m.group(1)}{lead}{_assign_mask(value)}{trail}"
        text = _SECRET_HEADER_RE.sub(_redact_secret_header, text)

    # Telegram bot tokens — pattern requires ":<token>" with digits prefix
    if ":" in text:
        def _redact_telegram(m):
            prefix = m.group(1) or ""
            digits = m.group(2)
            return f"{prefix}{digits}:***"
        text = _TELEGRAM_RE.sub(_redact_telegram, text)

    # Private key blocks
    if "BEGIN" in text and "-----" in text:
        text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    # Database connection string passwords. With code_file=True, a password
    # group that is a pure ``{...}`` brace expression is an f-string template
    # reference (e.g. f"postgresql://{user}:{pass}@{host}"), not a literal
    # credential — preserve it. Literal passwords are still redacted. The regex
    # forbids whitespace in the password group, so a single-line template's
    # group(2) is exactly the brace expression. See issue #33801.
    if "://" in text:
        if code_file:
            def _redact_db(m):
                pw = m.group(2)
                if pw.startswith("{") and pw.endswith("}"):
                    return m.group(0)
                return f"{m.group(1)}***{m.group(3)}"
            text = _DB_CONNSTR_RE.sub(_redact_db, text)
        else:
            text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

        # Bare-token userinfo in web/transport URLs: ``scheme://TOKEN@host``.
        # The git-remote-with-embedded-password shape from #6396. Only the
        # colon-less bare-token form is redacted — ``user:pass@`` and
        # query-string tokens are left to pass through (see the web-URL note
        # below). See _URL_BARE_TOKEN_RE for the false-positive guards.
        text = _URL_BARE_TOKEN_RE.sub(
            lambda m: f"{m.group(1)}{_mask_token(m.group(2))}{m.group(3)}",
            text,
        )

    # JWT tokens (eyJ... — base64-encoded JSON headers)
    if "eyJ" in text:
        text = _JWT_RE.sub(lambda m: _mask_token(m.group(0)), text)

    # NOTE: Web-URL redaction (query params + userinfo + HTTP access-log
    # request targets) is intentionally OFF. Many legitimate workflows pass
    # opaque tokens through query strings — magic-link checkouts, OAuth
    # callbacks the agent is meant to follow, pre-signed share URLs — and
    # blanket-redacting param values by name breaks those skills mid-flow.
    # Known credential shapes (sk-, ghp_, JWTs, etc.) inside URLs are still
    # caught by _PREFIX_RE and _JWT_RE above. DB connection-string passwords
    # are still caught by _DB_CONNSTR_RE. The ONE userinfo case still redacted
    # is the colon-less bare-token form ``scheme://TOKEN@host`` (#6396, handled
    # by _URL_BARE_TOKEN_RE in the ``://`` block above): a bare credential in
    # userinfo is never a round-trip workflow token (those live in the query
    # string), so masking it can't break a skill. The ``user:pass@`` form is
    # left to pass through per #34029.

    # Form-urlencoded bodies (only triggers on clean k=v&k=v inputs).
    if "&" in text and "=" in text:
        text = _redact_form_body(text)

    # E.164 phone numbers (Signal, WhatsApp)
    if "+" in text:
        def _redact_phone(m):
            phone = m.group(1)
            if len(phone) <= 8:
                return phone[:2] + "****" + phone[-2:]
            return phone[:4] + "****" + phone[-4:]
        text = _SIGNAL_PHONE_RE.sub(_redact_phone, text)

    return text


# Commands whose stdout is an environment-variable dump (KEY=value lines),
# NOT source code. For these, terminal-output redaction must run the
# ENV-assignment pass (code_file=False) so opaque tokens with no recognized
# vendor prefix (e.g. ``MY_SERVICE_TOKEN=abc123randomstring``) are still
# masked. For all other commands, code_file=True is used to avoid mangling
# legitimate source/config dumps (``MAX_TOKENS=100``, ``"apiKey": "x"``
# fixtures, ``postgresql://{user}`` f-string templates). See issue #43025.
_ENV_DUMP_COMMANDS = frozenset({"env", "printenv", "set", "export", "declare"})

# Commands that read file contents to stdout. A ``.env`` target is a credential
# dump (per AGENTS.md ``.env`` holds only secrets), so the ENV pass must run.
_FILE_READ_COMMANDS = frozenset({
    "cat", "head", "tail", "type", "bat", "less", "more", "nl",
    "zcat", "tac", "view", "batcat",
})
_SECRET_BEARING_FILE_BASENAMES = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zprofile", ".zlogin", ".zshenv",
})
_TEXT_FILE_READ_COMMANDS = frozenset({"grep", "awk", "sed"})


def _command_segments(command: str) -> list[str]:
    """Pipeline/sequence segments, split only on unquoted ``| ; &``.

    Quote-aware so ``awk '{print $1; print $2}'`` / ``grep 'foo|bar'`` stay
    one segment. Backslash is not an escape (Windows ``C:\\Users\\...``).
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in command:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            continue
        if ch in "|;&":
            seg = "".join(buf).strip()
            if seg:
                segments.append(seg)
            buf = []
            continue
        buf.append(ch)
    seg = "".join(buf).strip()
    if seg:
        segments.append(seg)
    return segments


def _command_reads_env_file(command: str | None) -> bool:
    """True if ``command`` reads a ``.env``-style file (by basename) to stdout.
    Defense-in-depth, not a boundary: indirect reads (``sudo cat .env``, ``$(cat
    .env)``, ``sed``/``awk``) are not detected, matching ``is_env_dump_command``."""
    if not command:
        return False
    for seg in _command_segments(command):
        tokens = seg.split()  # not shlex: it mangles Windows paths (``C:\Users\...\.env``)
        if not tokens or tokens[0] not in _FILE_READ_COMMANDS:
            continue
        for arg in tokens[1:]:
            if arg.startswith("-"):
                continue
            basename = arg.strip("\"'").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if basename.lower() in _ENV_FILE_BASENAMES:
                return True
    return False


_HERMES_HOME_PREFIXES = ("$HERMES_HOME/", "${HERMES_HOME}/")


def _is_hermes_config_basename(name: str) -> bool:
    """``config.yaml`` plus any ``config.yaml.<suffix>`` copy of it.

    Hermes writes ``backups/config/config.yaml.good.<stamp>`` /
    ``.corrupt.<stamp>.bak`` snapshots, and hand-made copies
    (``config.yaml.bak-<date>``, ``config.yaml.pre-<change>``) sit beside the
    live file — same contents, same secrets. Only consulted for paths already
    under a Hermes home, so arbitrary project YAML is unaffected.
    """
    return name == "config.yaml" or name.startswith("config.yaml.")


def _is_under_hermes_home(path: str) -> bool:
    """True when an absolute path sits under the active Hermes home or root.

    A resolved path (what the file tools hand over) never spells
    ``$HERMES_HOME``, and a home outside ``~/.hermes`` (``HERMES_HOME=/srv/h``,
    or a symlinked home that resolves elsewhere) carries no ``.hermes``
    segment. Compare against the resolved homes instead. Only reached for a
    ``config.yaml`` basename, so the resolve cost stays off the per-token
    command scan; relative paths are never resolved (their base is unknown).
    """
    from agent.file_safety import _hermes_home_path, _hermes_root_path

    try:
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            return False
        target = os.path.normcase(os.path.realpath(expanded))
    except (OSError, ValueError):
        return False
    for getter in (_hermes_home_path, _hermes_root_path):
        try:
            base = os.path.normcase(os.path.realpath(str(getter())))
        except (OSError, ValueError):
            continue
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def _is_secret_bearing_file_arg(arg: str) -> bool:
    """Recognize explicit Hermes config (and its backup copies) and standard
    shell startup paths. ``$HERMES_HOME/…`` / ``${HERMES_HOME}/…`` count as a
    Hermes home; any other unresolved ``$VAR`` path is not classified."""
    path = arg.strip("\"'").replace("\\", "/")
    hermes_home = False
    for prefix in _HERMES_HOME_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix):]
            hermes_home = True
            break
    if "$" in path:
        return False
    parts = [part.lower() for part in path.split("/") if part]
    if not parts:
        return False
    if parts[-1] in _SECRET_BEARING_FILE_BASENAMES:
        return True
    if not _is_hermes_config_basename(parts[-1]):
        return False
    return hermes_home or ".hermes" in parts[:-1] or _is_under_hermes_home(path)


def is_secret_file_path(path: str | os.PathLike | None) -> bool:
    """Classify a file-tool path (read_file / search_files) as secret-bearing.

    ``.env``-style basenames anywhere, shell rc files, and Hermes
    ``config.yaml`` (plus its backup copies) under a Hermes home — the same
    rule the terminal side applies to ``cat``/``grep`` targets, so the two
    surfaces cannot drift. Callers pass the RESOLVED path and set
    ``redact_sensitive_text(..., secret_file=True)`` on a hit.
    """
    if not path:
        return False
    text = str(path).replace("\\", "/")
    basename = text.rsplit("/", 1)[-1].lower()
    if basename in _ENV_FILE_BASENAMES:
        return True
    return _is_secret_bearing_file_arg(text)


def _command_reads_secret_bearing_file(command: str | None) -> bool:
    """True for direct stdout reads of known secret-bearing config files."""
    if not command or not isinstance(command, str):
        return False
    for seg in _command_segments(command):
        tokens = seg.split()  # preserve Windows path separators; see _command_reads_env_file
        if not tokens:
            continue
        reader = tokens[0].rsplit("/", 1)[-1].lower()
        if reader in _FILE_READ_COMMANDS:
            if any(_is_secret_bearing_file_arg(arg) for arg in tokens[1:] if not arg.startswith("-")):
                return True
            continue
        if reader in _TEXT_FILE_READ_COMMANDS:
            positional = [arg for arg in tokens[1:] if not arg.startswith("-")]
            if any(_is_secret_bearing_file_arg(arg) for arg in positional[1:]):
                return True
    return False


def is_env_dump_command(command: str | None) -> bool:
    """Return True if ``command`` dumps environment variables to stdout.

    Detects ``env`` / ``printenv`` / ``set`` / ``export`` / ``declare`` as the
    first token of any segment in a pipeline or sequence (``;`` / ``&&`` /
    ``||`` / ``|``). Conservative: a parse failure or anything unrecognized
    returns False (callers then fall back to the safer code_file=True path,
    which still masks prefix-shaped keys).
    """
    if not command or not isinstance(command, str):
        return False
    # Split on shell separators, then inspect the first token of each segment.
    segments = re.split(r"[|;&]+", command)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        if tokens and tokens[0] in _ENV_DUMP_COMMANDS:
            return True
    return False


def redact_terminal_output(
    output: str, command: str | None = None, *, force: bool = False
) -> str:
    """Redact secrets from terminal/process stdout.

    Single redaction policy for ALL terminal-output surfaces — foreground
    ``terminal`` results AND background ``process(action=poll/log/wait)``
    output — so they can't diverge. Picks ``code_file`` based on whether
    ``command`` is an environment dump:

    - env-dump command (``env``/``printenv``/``set``/``export``/``declare``)
      → ``code_file=False`` so the ENV-assignment pass masks opaque tokens.
    - file-read command targeting a ``.env`` file (``cat .env``,
      ``head .env.local``, etc.) → ``code_file=False`` for the same reason.
    - direct read of a secret-bearing config file (``~/.hermes/config.yaml``,
      profile ``config.yaml``, shell startup files like ``~/.bashrc``) via a
      file-read command or ``grep``/``awk``/``sed`` → ``code_file=False``.
      ``.env`` and secret-bearing reads also set ``secret_file=True`` (same
      semantics as a ``read_file`` of that file, minus the sentinel mask).
    - anything else (or unknown command) → ``code_file=True`` to avoid
      false positives on source/config dumps.

    ``force=True`` bypasses the global ``security.redact_secrets`` preference
    for safety boundaries that must never emit raw credentials.
    """
    if not output:
        return output
    secret_file = _command_reads_env_file(command) or _command_reads_secret_bearing_file(command)
    code_file = not (is_env_dump_command(command or "") or secret_file)
    redacted = redact_sensitive_text(
        output, force=force, code_file=code_file, secret_file=secret_file)
    # Source-preserving output still gets the Python-repr pass on high-confidence
    # diagnostic lines (pytest ``E   `` introspection, final exception lines): that
    # is where {'BRAVE_API_KEY': '…'} leaks, not in source dumps.
    if code_file and (force or _REDACT_ENABLED) and ":" in redacted and "'" in redacted:
        redacted = _redact_python_diagnostic_repr_fields(redacted)
    return redacted


# Substrings used to gate ``_PREFIX_RE`` execution. If none of these appear in
# the input string, the prefix regex cannot match anything, so we skip it.
# False positives are fine (they just run the regex, which then matches
# nothing) — the bound is "no false negatives" and that holds because every
# pattern in ``_PREFIX_PATTERNS`` has at least one of these as a literal
# substring of its leading characters.
#
# Derived automatically from ``_PREFIX_PATTERNS`` at module load time so a
# future PR that adds a new prefix to the regex list can't silently break
# the screen.

def _extract_literal_prefix(pattern: str) -> str:
    """Return the leading literal characters of a regex pattern.

    Stops at the first regex metacharacter (``[``, ``(``, ``\\``, ``.``,
    ``?``, ``*``, ``+``, ``|``, ``{``, ``^``, ``$``).  Returns the literal
    that any match of the pattern MUST contain as a substring, so the
    pre-screen never produces false negatives.
    """
    meta = "[(\\.?*+|{^$"
    for i, ch in enumerate(pattern):
        if ch in meta:
            return pattern[:i]
    return pattern


_PREFIX_SUBSTRINGS = tuple(
    _extract_literal_prefix(p) for p in _PREFIX_PATTERNS
)


def _has_known_prefix_substring(text: str) -> bool:
    """Return True if ``text`` contains any known credential prefix substring.

    Used as a cheap pre-check before invoking the expensive ``_PREFIX_RE``.
    """
    return any(p in text for p in _PREFIX_SUBSTRINGS)


_HTTP_METHOD_SUBSTRINGS = (
    "GET ",
    "POST ",
    "PUT ",
    "PATCH ",
    "DELETE ",
    "HEAD ",
    "OPTIONS ",
    "TRACE ",
    "CONNECT ",
)


def _has_http_method_substring(text: str) -> bool:
    """Cheap pre-check before scanning for access-log request targets."""
    upper = text.upper()
    return any(method in upper for method in _HTTP_METHOD_SUBSTRINGS)


class RedactingFormatter(logging.Formatter):
    """Log formatter that redacts secrets from all log messages."""

    def __init__(self, fmt=None, datefmt=None, style='%', **kwargs):
        super().__init__(fmt, datefmt, style, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_text(original)
