"""Package-manager uninstalls need approval (upstream #10199, 85ce25687e + ce0b10cb21).

`npm uninstall -g`, pnpm/yarn remove, `pip uninstall` and `brew uninstall`
remove software outside the project. The rules are anchored at command
position, so the same words as data (`echo`, `grep`, a commit message) stay
unprompted, and a global option may carry one operand
(`npm --prefix ./app uninstall x`).
"""

import pytest

from tools.approval import detect_dangerous_command


class TestPackageManagerUninstallApproval:
    @pytest.mark.parametrize("command", [
        "npm uninstall -g left-pad", "npm r left-pad", "pnpm un -g left-pad",
        "yarn global remove left-pad", "pip3 uninstall left-pad", "brew rm left-pad",
        "brew uninstall wget", "pip3 uninstall -y requests", "pip uninstall -y requests",
        "npm --prefix ./app uninstall left-pad", "pip --proxy http://p:1 uninstall -y requests",
        "cd app && yarn --cwd ./app remove left-pad",
        "sudo npm uninstall -g left-pad",
        "npm -g uninstall left-pad",
        # Local widening: versioned pip entry points.
        "pip3.12 uninstall -y requests",
    ])
    def test_uninstall_requires_approval(self, command):
        dangerous, key, _ = detect_dangerous_command(command)
        assert dangerous and key == "package manager uninstall", command

    @pytest.mark.parametrize("command", [
        "npm update -g left-pad", "pnpm add left-pad", "yarn install", "pip install left-pad",
        "brew upgrade left-pad", "npm install left-pad",
        'git commit -m "document npm uninstall usage"', 'echo "pip uninstall foo"',
        "echo uninstall", "grep uninstall notes.md", "grep -rn 'brew uninstall' docs/",
    ])
    def test_install_update_and_prose_stay_unprompted(self, command):
        assert detect_dangerous_command(command) == (False, None, None), command
