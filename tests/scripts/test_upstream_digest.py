"""Classification rules for scripts/upstream_digest.py.

The digest decides which upstream commits a security-only sync must look at.
Until 2026-09-24 it matched only the `security` type and reported 11 of ~50
security commits (HA-0006), so these tests pin the subject shapes upstream
actually uses. On 2026-10-06 the vocabulary and body-advisory rules were added
after a ReDoS fix filed as `fix(agent)` went unreported (HA-0013); the vocabulary
cases below are real upstream subjects.
"""

from pathlib import Path

import pytest

from scripts.upstream_digest import (
    is_security,
    landed_shas,
    load_triaged,
    matches_any,
    parse_log,
)


@pytest.mark.parametrize(
    "subject",
    [
        "security(vision): route local-file inputs through the shared guard",
        "security: make state databases and snapshots owner-only",
        "fix(security): redact secrets from config file reads",
        "ci(security): include photon sidecar lockfiles in OSV scan",
        "feat(security): protected agent-instruction files always require write approval",
        "fix(sec): move cryptography to 50.0.0",
        "feat(sec): add min-release-age = 2 wks in .npmrc",
        "fix(desktop): deny window-open side-effect opens (GHSA-9f4c-93c8-jc8g)",
        "deps: bump tornado 6.5.7 -> 6.5.8 (GHSA-5w76-955r-9v8r multipart DoS)",
        "fix(deps): pin foo past CVE-2026-12345",
        "fix(gateway,security): scope tokens",
        "fix(security)!: breaking hardening",
        "feat(approvals): user-defined deny rules that block commands even under yolo (#59164)",
        "feat(approval): flag cloud metadata-endpoint (IMDS) credential fetches for approval",
        "fix(approval): catch rm -rf behind env prefixes",
        "fix(redact): mask Fireworks token prefixes",
        "fix(redaction): scrub bot tokens from transport errors",
        "fix(ssrf): pin DNS for web fetches",
        # Security vocabulary under an ordinary scope (the HA-0013 gap).
        "fix(agent): anchor the Telegram token regex to a digit-run start (ReDoS)",
        "fix(gateway): redact secrets in background process completion output",
        "fix(kanban): guard kanban_attach_url against SSRF via tools.url_safety",
        "fix(compression): close the restore TOCTOU; fold review findings",
        "fix(mcp): close select-to-bind TOCTOU on the OAuth callback port",
        "fix(tools): symlink-safe exclusive creation for all spill/cache writers",
        "fix(tools): keep shell snapshots owner-only",
        "fix(docker): keep forwarded secret values out of world-readable argv",
        "fix(terminal): stop shared bash snapshot leaking HERMES_SESSION_* across sessions",
        "fix(delegation): prevent child HERMES_SESSION_ID leak into parent process env",
        "fix(cron): close NUL-padded script bypass in lifecycle guard",
        "fix: close indirect lifecycle guard bypasses",
        "fix(threat-scanner): close reviewer-noted ssh_access_write bypass shapes",
        "fix(tui-gateway): tools.* RPCs answer 4064 for traversal-shaped profile params",
        "fix(skills): flag Send child/workers context as exfil",
        "fix(secrets): redact command in process checkpoint file (#77484)",
        # A ReDoS fix filed as `perf` still names the security idea.
        "perf(redact): make the keyword scan linear (ReDoS)",
    ],
)
def test_security_subjects_are_caught(subject):
    assert is_security(subject)


@pytest.mark.parametrize(
    "subject",
    [
        # A security-named plugin or module in the scope is not a security fix.
        "refactor(plugins/teams_pipeline,spotify,security-guidance): phase-split pipeline",
        "refactor(tools): compact tirith_security + threat_patterns",
        # Tests and docs about security change no behaviour.
        "test(security): cover the new guard",
        "docs(security): describe the approval modes",
        "fix(macos): keep the TCC anchor alive across CVE-repair rotations",
        "chore: map RHODIZSECURITY contributor email",
        "Merge pull request #83404 from NousResearch/fix/blender-mcp-compromise",
        # Gate scopes on no-behaviour types stay out, like `security` does.
        "refactor(approval): compact the guard ladder",
        "test(redact): cover env-name variants",
        # Provider OAuth and the vault feature are deliberately not gate scopes.
        "fix(auth): refresh the Codex token before expiry",
        "feat(secrets): add a 1Password source",
        # The vocabulary used for something else (real upstream subjects).
        "fix(plugins): route inject_message to the TUI session_key queue",
        "fix(gateway): let internal events bypass topic lobby",
        "perf(relay): bypass inactive tool interception",
        "fix(mcp): stop leaking an unawaited watcher coroutine in the _watch_ok probe",
        "fix(browser): reap leaked agent-browser daemons whose owner is still alive",
        "fix(state): close leaked SessionDB connections on exception paths (#83226)",
        "feat(plugin-catalog): bump bot-forge to v0.7.0",
        "fix(desktop): key the failed-install Escape effect on a boolean, not the error string",
        "fix(mcp): cycle-guard cause/context traversal in transport classifier",
        # Vocabulary on a no-behaviour type is a test or doc, not a fix.
        "test(agent): cover the ReDoS-shaped digit run",
        "docs: explain the SSRF guard",
    ],
)
def test_non_security_subjects_are_not(subject):
    assert not is_security(subject)


def test_an_advisory_cited_only_in_the_body_counts():
    body = "Bumps the transitive axios entry.\n\nFixes GHSA-vh66-26gq-q6x8."
    assert is_security("chore(deps): refresh transitive axios lockfile entry to 1.20.0", body)
    assert is_security("fix(worktree): run the startup remove under the repo-safe git env",
                       "Follow-up to GHSA-7x36-8jrh-v4pw.")
    # A test or doc that mentions an advisory changes no behaviour.
    assert not is_security("test(deps): pin the axios advisory case", body)
    assert not is_security("chore(deps): refresh lockfile", "Routine refresh.")


def test_landed_shas_reads_both_trailer_styles():
    bodies = (
        "security(env): scope passthrough\n\n"
        "Ported from upstream 7138b9587a0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e (adapted: x)\n"
        "\n"
        "fix(security): pin DNS\n\n(cherry picked from commit 42626da1ce)\n"
    )
    assert landed_shas(bodies) == {
        "7138b9587a0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e",
        "42626da1ce",
    }


def test_matches_any_accepts_short_and_full_shas():
    full = "42626da1ce0123456789abcdef0123456789abcd"
    assert matches_any(full, {"42626da1ce"})
    assert matches_any("42626da1ce", {full})
    assert not matches_any(full, {"42626da1cf"})


def test_load_triaged_ignores_comments_and_blank_lines(tmp_path: Path):
    path = tmp_path / "triaged.txt"
    path.write_text(
        "# header\n\n"
        "2E08B778AB out of scope: Slack not used\n"
        "16332af60 not applicable  # trailing comment\n",
        encoding="utf-8",
    )
    assert load_triaged(path) == {
        "2e08b778ab": "out of scope: Slack not used",
        "16332af60": "not applicable",
    }


def test_load_triaged_missing_file_is_empty(tmp_path: Path):
    assert load_triaged(tmp_path / "absent.txt") == {}


def test_shipped_triaged_file_parses_with_a_reason_per_entry():
    triaged = load_triaged()
    assert triaged
    assert all(len(sha) >= 7 and reason for sha, reason in triaged.items())


def test_parse_log_splits_records_body_and_files():
    log = (
        "\x00aaa111\t2026-09-02\tfix(security): one\tstill the subject"
        "\x1fA body line.\n\ntools/not_a_file.py is prose here\n\x1e\n"
        "gateway/run.py\ntools/x.py\n"
        "\n\x00bbb222\t2026-09-03\tmerge commit with no files\x1f\x1e\n"
    )
    assert parse_log(log) == [
        ("aaa111", "2026-09-02", "fix(security): one\tstill the subject",
         "A body line.\n\ntools/not_a_file.py is prose here",
         ["gateway/run.py", "tools/x.py"]),
        ("bbb222", "2026-09-03", "merge commit with no files", "", []),
    ]


def test_json_report_lists_only_undecided_security(monkeypatch, capsys):
    """The estate reviewer consumes --json; pin its shape and the filtering."""
    import json
    import sys

    import scripts.upstream_digest as digest

    upstream_log = (
        "\x00" + "a" * 40 + "\t2026-09-02\tfix(security): undecided\x1f\x1e\ntools/x.py\n"
        "\x00" + "b" * 40 + "\t2026-09-03\tfix(security): ported already\x1f\x1e\ntools/y.py\n"
        "\x00" + "c" * 40 + "\t2026-09-04\tfix(sec): triaged away\x1f\x1e\n"
        "\x00" + "d" * 40 + "\t2026-09-05\tfeat(web): a feature\x1fNothing to see.\x1e\nweb/app.ts\n"
        "\x00" + "e" * 40 + "\t2026-09-06\tchore(deps): refresh lockfile"
        "\x1fClears GHSA-vh66-26gq-q6x8.\x1e\npackage-lock.json\n"
    )
    ours = "security: port\n\nPorted from upstream " + "b" * 40 + " (adapted)\n"

    def fake_run(*args):
        if args[0] == "log" and "--name-only" in args:
            return upstream_log
        if args[0] == "log":
            return ours
        raise AssertionError(args)

    monkeypatch.setattr(digest, "run", fake_run)
    monkeypatch.setattr(digest, "load_triaged", lambda: {"c" * 10: "out of scope"})
    monkeypatch.setattr(sys, "argv", ["upstream_digest.py", "--no-fetch", "--json"])
    assert digest.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["security"] == [
        {"sha": "a" * 40, "date": "2026-09-02", "subject": "fix(security): undecided"},
        {"sha": "e" * 40, "date": "2026-09-06", "subject": "chore(deps): refresh lockfile"},
    ]
    assert report["security_landed"] == 1
    assert report["security_triaged"] == 1
    assert report["new_commits"] == 5
    assert report["other"] == 1
