"""Classification rules for scripts/upstream_digest.py.

The digest decides which upstream commits a security-only sync must look at.
Until 2026-09-24 it matched only the `security` type and reported 11 of ~50
security commits (HA-0006), so these tests pin the subject shapes upstream
actually uses.
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
        "fix(gateway): redact secrets in background process completion output",
        "Merge pull request #83404 from NousResearch/fix/blender-mcp-compromise",
    ],
)
def test_non_security_subjects_are_not(subject):
    assert not is_security(subject)


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


def test_parse_log_splits_records_and_files():
    log = (
        "\x00aaa111\tfix(security): one\ngateway/run.py\ntools/x.py\n"
        "\n\x00bbb222\tmerge commit with no files\n"
    )
    assert parse_log(log) == [
        ("aaa111", "fix(security): one", ["gateway/run.py", "tools/x.py"]),
        ("bbb222", "merge commit with no files", []),
    ]
