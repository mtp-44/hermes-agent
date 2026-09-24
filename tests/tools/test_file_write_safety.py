"""Tests for file write safety and HERMES_WRITE_SAFE_ROOT sandboxing.

Based on PR #1085 by ismoilh (salvaged).
"""

import os
from pathlib import Path

import pytest

from tools.file_operations import _is_write_denied


class TestStaticDenyList:
    """Basic sanity checks for the static write deny list."""

    def test_temp_file_not_denied_by_default(self, tmp_path: Path):
        target = tmp_path / "regular.txt"
        assert _is_write_denied(str(target)) is False

    def test_ssh_key_is_denied(self):
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True

    def test_etc_shadow_is_denied(self):
        assert _is_write_denied("/etc/shadow") is True


class TestSafeWriteRoot:
    """HERMES_WRITE_SAFE_ROOT should sandbox writes to a specific subtree."""

    def test_writes_inside_safe_root_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        child = safe_root / "subdir" / "file.txt"
        os.makedirs(child.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(child)) is False

    def test_writes_to_safe_root_itself_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(safe_root)) is False

    def test_writes_outside_safe_root_are_denied(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        outside = tmp_path / "other" / "file.txt"
        os.makedirs(safe_root, exist_ok=True)
        os.makedirs(outside.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(outside)) is True

    def test_safe_root_env_ignores_empty_value(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", "")
        assert _is_write_denied(str(target)) is False

    def test_safe_root_unset_allows_all(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
        assert _is_write_denied(str(target)) is False

    def test_safe_root_with_tilde_expansion(self, tmp_path: Path, monkeypatch):
        """~ in HERMES_WRITE_SAFE_ROOT should be expanded."""
        # Use a real subdirectory of tmp_path so we can test tilde-style paths
        safe_root = tmp_path / "workspace"
        inside = safe_root / "file.txt"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(inside)) is False

    def test_safe_root_does_not_override_static_deny(self, tmp_path: Path, monkeypatch):
        """Even if a static-denied path is inside the safe root, it's still denied."""
        # Point safe root at home to include ~/.ssh
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", os.path.expanduser("~"))
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True


class TestMultipleSafeWriteRoots:
    """HERMES_WRITE_SAFE_ROOT with multiple colon-separated directories."""

    def test_write_inside_first_root_allowed(self, tmp_path: Path, monkeypatch):
        root_a = tmp_path / "workspace_a"
        root_b = tmp_path / "workspace_b"
        child = root_a / "subdir" / "file.txt"
        os.makedirs(child.parent, exist_ok=True)
        os.makedirs(root_b, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{root_a}{os.pathsep}{root_b}")
        assert _is_write_denied(str(child)) is False

    def test_write_inside_second_root_allowed(self, tmp_path: Path, monkeypatch):
        root_a = tmp_path / "workspace_a"
        root_b = tmp_path / "workspace_b"
        child = root_b / "subdir" / "file.txt"
        os.makedirs(child.parent, exist_ok=True)
        os.makedirs(root_a, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{root_a}{os.pathsep}{root_b}")
        assert _is_write_denied(str(child)) is False

    def test_write_outside_all_roots_denied(self, tmp_path: Path, monkeypatch):
        root_a = tmp_path / "workspace_a"
        root_b = tmp_path / "workspace_b"
        outside = tmp_path / "other" / "file.txt"
        os.makedirs(root_a, exist_ok=True)
        os.makedirs(root_b, exist_ok=True)
        os.makedirs(outside.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{root_a}{os.pathsep}{root_b}")
        assert _is_write_denied(str(outside)) is True

    def test_trailing_separator_ignored(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "workspace"
        inside = root / "file.txt"
        os.makedirs(root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{root}{os.pathsep}")
        assert _is_write_denied(str(inside)) is False

    def test_leading_separator_ignored(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "workspace"
        inside = root / "file.txt"
        os.makedirs(root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{os.pathsep}{root}")
        assert _is_write_denied(str(inside)) is False

    def test_double_separator_ignored(self, tmp_path: Path, monkeypatch):
        root_a = tmp_path / "workspace_a"
        root_b = tmp_path / "workspace_b"
        os.makedirs(root_a, exist_ok=True)
        os.makedirs(root_b, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", f"{root_a}{os.pathsep}{os.pathsep}{root_b}")
        # Both roots should still be active
        assert _is_write_denied(str(root_a / "file.txt")) is False
        assert _is_write_denied(str(root_b / "file.txt")) is False

    def test_all_separators_yields_empty_set(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", os.pathsep * 3)
        assert _is_write_denied(str(target)) is False

    def test_static_deny_still_wins_with_multiple_roots(self, tmp_path: Path, monkeypatch):
        """Static deny list takes priority even when multiple safe roots include home."""
        root = tmp_path / "workspace"
        os.makedirs(root, exist_ok=True)

        monkeypatch.setenv(
            "HERMES_WRITE_SAFE_ROOT",
            f"{root}{os.pathsep}{os.path.expanduser('~')}",
        )
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True

    def test_duplicate_roots_deduplicated(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "workspace"
        inside = root / "file.txt"
        os.makedirs(root, exist_ok=True)

        monkeypatch.setenv(
            "HERMES_WRITE_SAFE_ROOT",
            f"{root}{os.pathsep}{root}",
        )
        assert _is_write_denied(str(inside)) is False


class TestCheckSensitivePathMacOSBypass:
    """Verify _check_sensitive_path blocks /private/etc paths (issue #8734)."""

    def test_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/etc/hosts") is not None

    def test_private_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/hosts") is not None

    def test_private_etc_ssh_config_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/ssh/sshd_config") is not None

    def test_private_var_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/db/something") is not None

    def test_boot_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/boot/grub/grub.cfg") is not None

    def test_safe_path_allowed(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/tmp/safe_file.txt") is None


class TestAtomicWrite:
    """write_file / patch land via a temp-file + atomic rename.

    The invariant: a write that fails partway NEVER corrupts the existing
    file, and the swap is a real rename (so a reader either sees the full
    old content or the full new content, never a half-written file). These
    run against a real LocalEnvironment so the actual shell script executes.
    """

    @pytest.fixture
    def ops(self, tmp_path: Path):
        from tools.environments.local import LocalEnvironment
        from tools.file_operations import ShellFileOperations
        env = LocalEnvironment(cwd=str(tmp_path))
        return ShellFileOperations(env, cwd=str(tmp_path))

    def test_overwrite_changes_inode(self, ops, tmp_path: Path):
        # A real rename allocates a new inode for the target; an in-place
        # rewrite would keep the same inode. This proves the swap is atomic.
        target = tmp_path / "f.txt"
        target.write_text("v1", encoding="utf-8")
        ino_before = os.stat(target).st_ino
        res = ops.write_file(str(target), "v2 content")
        assert res.error is None, res.error
        assert target.read_text(encoding="utf-8") == "v2 content"
        assert os.stat(target).st_ino != ino_before

    def test_overwrite_preserves_mode(self, ops, tmp_path: Path):
        target = tmp_path / "perms.txt"
        target.write_text("old")
        os.chmod(target, 0o640)
        res = ops.write_file(str(target), "new")
        assert res.error is None, res.error
        assert (os.stat(target).st_mode & 0o777) == 0o640

    def test_failed_write_leaves_original_intact(self, ops, tmp_path: Path):
        # A read-only parent directory means the temp file can't be created,
        # so the write fails BEFORE any rename. The original must survive
        # byte-for-byte and no temp file may be left behind.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses directory permission bits")
        locked = tmp_path / "locked"
        locked.mkdir()
        target = locked / "f.txt"
        target.write_text("ORIGINAL\n")
        os.chmod(locked, 0o500)  # r-x: cannot create entries inside
        try:
            res = ops.write_file(str(target), "SHOULD NOT LAND")
        finally:
            os.chmod(locked, 0o700)  # restore for cleanup
        assert res.error is not None
        assert target.read_text() == "ORIGINAL\n"
        assert [p for p in os.listdir(locked) if ".hermes-tmp" in p] == []

    def test_no_temp_file_leaked_on_success(self, ops, tmp_path: Path):
        target = tmp_path / "f.txt"
        ops.write_file(str(target), "hello\n")
        assert [p for p in os.listdir(tmp_path) if ".hermes-tmp" in p] == []

    def test_special_chars_roundtrip(self, ops, tmp_path: Path):
        target = tmp_path / "special.txt"
        tricky = "q 'single' \"double\" $VAR `cmd` \\back\nünïcödé 日本語\n"
        res = ops.write_file(str(target), tricky)
        assert res.error is None, res.error
        assert target.read_text(encoding="utf-8") == tricky

    def test_patch_routes_through_atomic_write(self, ops, tmp_path: Path):
        target = tmp_path / "edit.py"
        target.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        os.chmod(target, 0o600)
        res = ops.patch_replace(str(target), "b = 2", "b = 22")
        assert res.success, res.error
        assert target.read_text(encoding="utf-8") == "a = 1\nb = 22\nc = 3\n"
        assert (os.stat(target).st_mode & 0o777) == 0o600


class TestBomHandling:
    """UTF-8 BOM is stripped on read and preserved across write/patch.

    A BOM (U+FEFF, bytes EF BB BF) is an invisible leading marker some
    Windows editors prepend. The agent should never see it in read output,
    but a file that had one on disk must keep it after an edit so the byte
    signature is preserved.
    """

    BOM = "\ufeff"

    @pytest.fixture
    def ops(self, tmp_path: Path):
        from tools.environments.local import LocalEnvironment
        from tools.file_operations import ShellFileOperations
        env = LocalEnvironment(cwd=str(tmp_path))
        return ShellFileOperations(env, cwd=str(tmp_path))

    def test_helpers(self):
        from tools.file_operations import _strip_bom, _has_bom
        assert _strip_bom("\ufeffhello") == ("hello", True)
        assert _strip_bom("hello") == ("hello", False)
        assert _strip_bom("") == ("", False)
        # mid-string BOM is data, not a marker — left alone
        assert _strip_bom("a\ufeffb") == ("a\ufeffb", False)
        assert _has_bom("\ufeffx") is True
        assert _has_bom("x") is False
        assert _has_bom(None) is False

    def test_read_strips_bom(self, ops, tmp_path: Path):
        target = tmp_path / "bom.py"
        # Write raw bytes with a real UTF-8 BOM prefix.
        target.write_bytes(self.BOM.encode("utf-8") + b"import os\nx = 1\n")
        res = ops.read_file(str(target))
        assert res.error is None, res.error
        # Line 1 content must NOT carry the phantom U+FEFF.
        first_line = res.content.split("\n", 1)[0]
        assert self.BOM not in first_line
        assert first_line.endswith("import os")

    def test_read_raw_strips_bom(self, ops, tmp_path: Path):
        target = tmp_path / "bom.txt"
        target.write_bytes(self.BOM.encode("utf-8") + b"hello\nworld\n")
        res = ops.read_file_raw(str(target))
        assert res.error is None, res.error
        assert not res.content.startswith(self.BOM)
        assert res.content == "hello\nworld\n"

    def test_write_preserves_bom(self, ops, tmp_path: Path):
        # Existing file has a BOM; agent rewrites with BOM-less content.
        target = tmp_path / "config.txt"
        target.write_bytes(self.BOM.encode("utf-8") + b"old\n")
        res = ops.write_file(str(target), "new content\n")
        assert res.error is None, res.error
        raw = target.read_bytes()
        assert raw.startswith(self.BOM.encode("utf-8"))  # BOM restored
        assert raw == self.BOM.encode("utf-8") + b"new content\n"

    def test_write_no_bom_when_original_had_none(self, ops, tmp_path: Path):
        target = tmp_path / "plain.txt"
        target.write_text("old\n")
        res = ops.write_file(str(target), "new\n")
        assert res.error is None, res.error
        assert not target.read_bytes().startswith(self.BOM.encode("utf-8"))

    def test_write_does_not_double_bom(self, ops, tmp_path: Path):
        # If content already carries a BOM and the file had one, don't add a
        # second.
        target = tmp_path / "config.txt"
        target.write_bytes(self.BOM.encode("utf-8") + b"old\n")
        res = ops.write_file(str(target), self.BOM + "new\n")
        assert res.error is None, res.error
        raw = target.read_bytes()
        # exactly one BOM
        assert raw == self.BOM.encode("utf-8") + b"new\n"

    def test_patch_roundtrip_preserves_bom(self, ops, tmp_path: Path):
        target = tmp_path / "edit.py"
        target.write_bytes(self.BOM.encode("utf-8") + b"a = 1\nb = 2\nc = 3\n")
        res = ops.patch_replace(str(target), "b = 2", "b = 22")
        assert res.success, res.error
        raw = target.read_bytes()
        assert raw.startswith(self.BOM.encode("utf-8"))  # marker survived
        assert raw == self.BOM.encode("utf-8") + b"a = 1\nb = 22\nc = 3\n"

    def test_patch_matches_first_line_through_bom(self, ops, tmp_path: Path):
        # The whole point: an edit targeting the BOM-prefixed first line
        # must match cleanly (the matcher sees BOM-stripped content).
        target = tmp_path / "mod.py"
        target.write_bytes(self.BOM.encode("utf-8") + b"import os\nimport sys\n")
        res = ops.patch_replace(str(target), "import os", "import os, json")
        assert res.success, res.error
        raw = target.read_bytes()
        assert raw == self.BOM.encode("utf-8") + b"import os, json\nimport sys\n"


class TestProtectedInstructionFiles:
    """Writes to agent-instruction files ALWAYS require approval.

    AGENTS.md / CLAUDE.md / SOUL.md / .cursorrules / project-local .hermes
    config steer future agent behavior, so a prompt-injected agent writing
    them is a persistence vector. The gate must ask the human every time —
    even under yolo/auto-approve — and fail closed when no human channel
    exists. Ported from: RooCodeInc/Roo-Code RooProtectedController
    (Apache-2.0); symlink lesson from #41351.
    """

    @pytest.fixture(autouse=True)
    def _gate_on(self, monkeypatch):
        import tools.file_tools as ft
        monkeypatch.setattr(
            ft, "_protected_instruction_config", lambda: (True, [])
        )
        # Fork: our blanket "/private/var/" sensitive prefix refuses every
        # macOS tmp_path (/var/folders realpaths there) before this gate is
        # reached. Use upstream's narrowed prefixes here so the tests exercise
        # the protected-file gate rather than the unrelated system-path block.
        monkeypatch.setattr(ft, "_SENSITIVE_PATH_PREFIXES", tuple(
            p for p in ft._SENSITIVE_PATH_PREFIXES if p != "/private/var/"
        ) + ("/private/var/db/", "/private/var/root/"))
        yield

    @pytest.fixture
    def approvals(self, monkeypatch):
        """Install a CLI approval callback; record calls; scripted answers."""
        from tools.terminal_tool import set_approval_callback
        state = {"calls": [], "answer": "deny"}

        def cb(command, description, **kwargs):
            state["calls"].append(
                {"command": command, "description": description, **kwargs}
            )
            return state["answer"]

        set_approval_callback(cb)
        yield state
        set_approval_callback(None)

    def _write(self, path, content="injected"):
        import json
        from tools.file_tools import write_file_tool
        return json.loads(write_file_tool(str(path), content))

    # ---- core behavior -------------------------------------------------

    @pytest.mark.parametrize(
        "name", ["AGENTS.md", "CLAUDE.md", "SOUL.md", ".cursorrules"]
    )
    def test_deny_blocks_write(self, tmp_path, approvals, name):
        target = tmp_path / name
        approvals["answer"] = "deny"
        res = self._write(target)
        assert res.get("error"), res
        assert "BLOCKED" in res["error"]
        assert not target.exists()
        assert len(approvals["calls"]) == 1

    def test_approve_once_allows_write(self, tmp_path, approvals):
        target = tmp_path / "AGENTS.md"
        approvals["answer"] = "once"
        res = self._write(target, "approved content")
        assert not res.get("error"), res
        assert target.read_text(encoding="utf-8") == "approved content"
        assert len(approvals["calls"]) == 1

    def test_prompts_even_under_yolo(self, tmp_path, approvals, monkeypatch):
        """The whole point: auto-approve/yolo must NOT bypass this gate."""
        import tools.approval as A
        monkeypatch.setattr(A, "_YOLO_MODE_FROZEN", True)
        target = tmp_path / "AGENTS.md"
        approvals["answer"] = "deny"
        res = self._write(target)
        assert res.get("error") and "BLOCKED" in res["error"]
        assert not target.exists()
        assert len(approvals["calls"]) == 1, "yolo bypassed the protected gate"

    def test_second_write_prompts_again(self, tmp_path, approvals):
        """One-operation approval: no session stickiness."""
        target = tmp_path / "AGENTS.md"
        approvals["answer"] = "once"
        self._write(target)
        self._write(target, "second")
        assert len(approvals["calls"]) == 2

    def test_cli_prompt_is_told_no_scope_persists(self, tmp_path, approvals):
        """The prompt must not advertise a scope this gate discards.

        Since nothing is persisted, a rendered "session"/"always" option
        re-prompts on the very next write and reads as a broken gate
        (upstream 165d1849e2).
        """
        approvals["answer"] = "once"
        self._write(tmp_path / "SOUL.md")
        call = approvals["calls"][0]
        assert call["allow_session"] is False
        assert call["allow_permanent"] is False

    def test_regular_file_never_prompts(self, tmp_path, approvals):
        res = self._write(tmp_path / "notes.md", "hello")
        assert not res.get("error"), res
        assert approvals["calls"] == []

    def test_no_human_fails_closed(self, tmp_path):
        # No approval callback registered, not gateway → block, don't hang.
        target = tmp_path / "AGENTS.md"
        res = self._write(target)
        assert res.get("error") and "BLOCKED" in res["error"]
        assert not target.exists()

    def test_config_disabled_skips_gate(self, tmp_path, approvals, monkeypatch):
        import tools.file_tools as ft
        monkeypatch.setattr(
            ft, "_protected_instruction_config", lambda: (False, [])
        )
        res = self._write(tmp_path / "AGENTS.md", "ok")
        assert not res.get("error"), res
        assert approvals["calls"] == []

    def test_extra_patterns_from_config(self, tmp_path, approvals, monkeypatch):
        import tools.file_tools as ft
        monkeypatch.setattr(
            ft, "_protected_instruction_config", lambda: (True, ["*.mdc"])
        )
        approvals["answer"] = "deny"
        res = self._write(tmp_path / "rules.mdc")
        assert res.get("error") and "BLOCKED" in res["error"]

    # ---- adversarial path shapes ----------------------------------------

    def test_symlink_to_protected_file_is_gated(self, tmp_path, approvals):
        """#41351 lesson: realpath first — innocent name, protected target."""
        real = tmp_path / "AGENTS.md"
        real.write_text("original", encoding="utf-8")
        link = tmp_path / "innocent.txt"
        link.symlink_to(real)
        approvals["answer"] = "deny"
        res = self._write(link, "injected")
        assert res.get("error") and "BLOCKED" in res["error"]
        assert real.read_text(encoding="utf-8") == "original"

    def test_case_variant_is_gated(self, tmp_path, approvals):
        approvals["answer"] = "deny"
        res = self._write(tmp_path / "agents.MD")
        assert res.get("error") and "BLOCKED" in res["error"]

    def test_relative_traversal_is_gated(self, tmp_path, approvals, monkeypatch):
        (tmp_path / "x").mkdir()
        monkeypatch.chdir(tmp_path)
        approvals["answer"] = "deny"
        res = self._write("./x/../AGENTS.md")
        assert res.get("error") and "BLOCKED" in res["error"]
        assert not (tmp_path / "AGENTS.md").exists()

    def test_arbitrary_directory_basename_is_gated(self, tmp_path, approvals):
        """Any-directory scope: project-context files load from cwd trees."""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        approvals["answer"] = "deny"
        res = self._write(deep / "CLAUDE.md")
        assert res.get("error") and "BLOCKED" in res["error"]

    def test_project_local_hermes_dir_is_gated(self, tmp_path, approvals):
        proj = tmp_path / "proj" / ".hermes"
        proj.mkdir(parents=True)
        approvals["answer"] = "deny"
        res = self._write(proj / "config.yaml")
        assert res.get("error") and "BLOCKED" in res["error"]

    def test_checkout_nested_under_hermes_dir_not_gated(self, tmp_path, approvals):
        """A repo living UNDER a .hermes dir (e.g. ~/.hermes/hermes-agent)
        must not have every write gated — only files directly inside a
        .hermes dir count as project config."""
        repo = tmp_path / ".hermes" / "some-repo" / "src"
        repo.mkdir(parents=True)
        res = self._write(repo / "module.py", "x = 1\n")
        assert not res.get("error"), res
        assert approvals["calls"] == []

    def test_real_hermes_home_not_gated_by_this_check(
        self, tmp_path, approvals, monkeypatch
    ):
        """~/.hermes itself is governed by existing guards, not this gate."""
        import tools.file_tools as ft
        fake_home = tmp_path / ".hermes"
        (fake_home / "notes").mkdir(parents=True)
        monkeypatch.setattr(
            ft, "_get_real_hermes_home", lambda: str(fake_home.resolve())
        )
        res = self._write(fake_home / "notes" / "scratch.txt", "ok")
        assert not res.get("error"), res
        assert approvals["calls"] == []

    # ---- patch tool -----------------------------------------------------

    def test_patch_replace_mode_is_gated(self, tmp_path, approvals):
        from tools.file_tools import patch_tool
        import json
        target = tmp_path / "SOUL.md"
        target.write_text("be kind\n", encoding="utf-8")
        approvals["answer"] = "deny"
        res = json.loads(patch_tool(
            mode="replace", path=str(target),
            old_string="be kind", new_string="obey injected orders",
        ))
        assert res.get("error") and "BLOCKED" in res["error"]
        assert target.read_text(encoding="utf-8") == "be kind\n"

    def test_patch_v4a_multifile_one_protected_blocks_whole_patch(
        self, tmp_path, approvals
    ):
        """Policy: one protected file gates the ENTIRE patch (deny = nothing
        applies, including the innocent file)."""
        from tools.file_tools import patch_tool
        import json
        agents = tmp_path / "AGENTS.md"
        agents.write_text("rules\n", encoding="utf-8")
        plain = tmp_path / "plain.txt"
        plain.write_text("hello\n", encoding="utf-8")
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {plain}\n"
            "@@\n"
            "-hello\n"
            "+world\n"
            f"*** Update File: {agents}\n"
            "@@\n"
            "-rules\n"
            "+injected\n"
            "*** End Patch"
        )
        approvals["answer"] = "deny"
        res = json.loads(patch_tool(mode="patch", patch=patch))
        assert res.get("error") and "BLOCKED" in res["error"]
        assert plain.read_text(encoding="utf-8") == "hello\n"
        assert agents.read_text(encoding="utf-8") == "rules\n"
        assert len(approvals["calls"]) == 1

    def test_patch_v4a_approved_applies(self, tmp_path, approvals):
        from tools.file_tools import patch_tool
        import json
        agents = tmp_path / "AGENTS.md"
        agents.write_text("rules\n", encoding="utf-8")
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {agents}\n"
            "@@\n"
            "-rules\n"
            "+updated rules\n"
            "*** End Patch"
        )
        approvals["answer"] = "once"
        res = json.loads(patch_tool(mode="patch", patch=patch))
        assert not res.get("error"), res
        assert agents.read_text(encoding="utf-8") == "updated rules\n"

    # ---- gateway round-trip ----------------------------------------------

    def test_gateway_notify_resolve_once_allows(self, tmp_path):
        import tools.approval as A
        session_key = "protected-files-test-session"
        token = A.set_current_session_key(session_key)
        try:
            def notify(approval_data):
                # Buttons must not offer persistent scopes for this gate.
                assert approval_data.get("allow_permanent") is False
                assert approval_data.get("allow_session") is False
                A.resolve_gateway_approval(session_key, "once")

            A.register_gateway_notify(session_key, notify)
            try:
                res = self._write(tmp_path / "AGENTS.md", "gateway approved")
                assert not res.get("error"), res
                assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "gateway approved"
            finally:
                A.unregister_gateway_notify(session_key)
        finally:
            A.reset_current_session_key(token)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
