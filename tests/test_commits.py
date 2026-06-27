"""Tests for git commit history capture."""
import shutil
import subprocess
from pathlib import Path

import pytest


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_scanner_captures_commits(tmp_path):
    """Scanner should read recent commits and the GitHub name from a repo."""
    from code_hub.scanner import ProjectScanner

    repo = tmp_path / "demo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Tester"], repo)
    _git(["remote", "add", "origin", "git@github.com:user/demo.git"], repo)

    (repo / "main.py").write_text("print('hello')\n")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "initial commit"], repo)

    git_info = ProjectScanner()._get_git_info(repo)

    assert git_info.is_repo
    assert git_info.github_name == "user/demo"
    assert len(git_info.commits) == 1
    assert git_info.commits[0].message == "initial commit"
    assert git_info.commits[0].author == "Tester"
    assert git_info.last_commit_at is not None


def test_commit_parser_handles_messages_with_separators():
    """The field separator must not be confused by characters in the subject."""
    from code_hub.scanner import ProjectScanner

    line = "abc123\x1fabc1\x1fAlice\x1f2024-01-02T03:04:05+00:00\x1ffix: tidy up"
    commits = ProjectScanner._parse_commits(line)

    assert len(commits) == 1
    assert commits[0].sha == "abc123"
    assert commits[0].short_sha == "abc1"
    assert commits[0].message == "fix: tidy up"
    assert commits[0].committed_at is not None
