"""Tests for CLI commands."""
import pytest
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent


class TestCLI:
    def _run_cli(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )
        return result

    def test_vocab_command(self):
        result = self._run_cli("vocab")
        assert result.returncode == 0
        assert "HSK 1" in result.stdout
        assert "HSK 7-9" in result.stdout

    def test_validate_passing_text(self, tmp_path):
        text_file = tmp_path / "test.txt"
        text_file.write_text("我是学生。我在学校学习。", encoding="utf-8")
        result = self._run_cli("validate", str(text_file), "-l", "1")
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_validate_failing_text(self, tmp_path):
        text_file = tmp_path / "test.txt"
        text_file.write_text("人工智能技术改变社会经济结构", encoding="utf-8")
        result = self._run_cli("validate", str(text_file), "-l", "1")
        assert result.returncode == 1
        assert "FAIL" in result.stdout

    def test_analyze_command(self, tmp_path):
        text_file = tmp_path / "test.txt"
        text_file.write_text("我是学生。今天天气很好。", encoding="utf-8")
        result = self._run_cli("analyze", str(text_file), "-l", "1")
        assert result.returncode == 0
        assert "Coverage" in result.stdout

    def test_coverage_command(self, tmp_path):
        text_file = tmp_path / "test.txt"
        text_file.write_text("我在学校学习中文。", encoding="utf-8")
        result = self._run_cli("coverage", str(text_file))
        assert result.returncode == 0
        assert "HSK 1" in result.stdout
