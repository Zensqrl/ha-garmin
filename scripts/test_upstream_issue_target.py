"""Offline behavioral tests for upstream-sync issue operations."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class UpstreamIssueTargetTests(unittest.TestCase):
    def run_case(
        self, *, ancestor=False, existing="", resolved=None, read_failure=False
    ):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/upstream-sync.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("GH_REPO: ${{ github.repository }}", workflow)
        body = workflow.split("      - name: Assess drift and file issue\n", 1)[1]
        script = body.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        bash = shutil.which("bash")
        if os.name == "nt":
            bash = r"C:\Program Files\Git\bin\bash.exe"
        if not bash or not Path(bash).exists():
            self.skipTest("Bash is required")
        with tempfile.TemporaryDirectory() as temporary:
            # Redirect the workflow scratch file into isolated test state.
            script = script.replace("/tmp/issue-body.md", "issue-body.md")
            setup = r"""
gh() {
  [ "$GH_REPO" = "$GITHUB_REPOSITORY" ] || return 92
  printf '%s\n' "$*" >> commands.log
  if [ "$1 $2" = 'repo view' ]; then
    [ "$READ_FAILURE" = 0 ] || return 93
    printf '%s\n' "$RESOLVED"
  elif [ "$1 $2" = 'issue list' ]; then
    printf '%s' "$EXISTING"
  fi
}
git() {
  case "$1" in
    merge-base) [ "$ANCESTOR" = 1 ] ;;
    rev-list) echo 2 ;;
    log) echo 'abcdef synthetic upstream change' ;;
    diff) : ;;
    merge) : ;;
    *) return 94 ;;
  esac
}
"""
            env = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "SystemRoot", "TEMP", "TMP"}
            }
            env.update(
                GH_REPO="Zensqrl/test-fork",
                GITHUB_REPOSITORY="Zensqrl/test-fork",
                UPSTREAM_REPO="upstream/test-fork",
                UPSTREAM_BRANCH="main",
                FORK_BRANCH="main",
                ISSUE_TITLE_PREFIX="Upstream sync:",
                ANCESTOR=str(int(ancestor)),
                EXISTING=existing,
                RESOLVED=resolved or "Zensqrl/test-fork",
                READ_FAILURE=str(int(read_failure)),
            )
            result = subprocess.run(
                [bash, "-e", "-o", "pipefail", "-c", setup + script],
                cwd=temporary,
                env=env,
                capture_output=True,
                text=True,
            )
            commands = (Path(temporary) / "commands.log").read_text().splitlines()
            return result, commands

    def test_create(self):
        result, commands = self.run_case()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(line.startswith("issue create ") for line in commands))

    def test_edit(self):
        result, commands = self.run_case(existing="42")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(line.startswith("issue edit 42 ") for line in commands))

    def test_close(self):
        result, commands = self.run_case(ancestor=True, existing="42")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(line.startswith("issue close 42 ") for line in commands))

    def test_current_without_issue(self):
        result, commands = self.run_case(ancestor=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            any(
                line.startswith(("issue create", "issue edit", "issue close"))
                for line in commands
            )
        )

    def test_wrong_target_stops_before_issue_lookup(self):
        result, commands = self.run_case(resolved="upstream/test-fork")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(commands), 1)

    def test_failed_target_read_stops_before_issue_lookup(self):
        result, commands = self.run_case(read_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(commands), 1)


if __name__ == "__main__":
    unittest.main()
