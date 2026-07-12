"""Regression test: tool outputs MUST remain text, never filtered."""
import pytest
from extractors.ingestion_filter import IngestionFilter


class TestToolOutputPassthrough:
    """Tool outputs must ALWAYS pass the ingestion filter as text."""

    @pytest.fixture
    def filt(self):
        return IngestionFilter()

    def test_short_stdout_passthrough(self, filt):
        ok, reason = filt.should_ingest("OK", "tool", "tool_output")
        assert ok, f"Short stdout rejected: {reason}"
        assert reason == "tool_output_passthrough"

    def test_long_git_log_passthrough(self, filt):
        content = "\n".join(f"abc{i:04d} commit message {i}" for i in range(20))
        ok, reason = filt.should_ingest(content, "tool", "tool_output")
        assert ok, f"Git log rejected: {reason}"

    def test_traceback_passthrough(self, filt):
        content = "Traceback (most recent call last):\n  File 'x.py', line 42\nValueError: bad"
        ok, reason = filt.should_ingest(content, "tool", "tool_output")
        assert ok, f"Traceback rejected: {reason}"

    def test_markdown_passthrough(self, filt):
        ok, _ = filt.should_ingest("# Title\n## Section\ncontent", "tool", "tool_result")
        assert ok

    def test_unicode_passthrough(self, filt):
        ok, _ = filt.should_ingest("Привет мир\n你好世界\n🎉", "tool", "tool_output")
        assert ok

    def test_json_tool_output_passthrough(self, filt):
        ok, _ = filt.should_ingest('{"results": [1,2,3], "count": 3}', "tool", "tool_output")
        assert ok

    def test_ansi_escape_passthrough(self, filt):
        ok, _ = filt.should_ingest("\x1b[32mPASSED\x1b[0m\n\x1b[31mFAILED\x1b[0m", "tool", "tool_output")
        assert ok

    def test_pytest_output_passthrough(self, filt):
        content = "test_a PASSED\ntest_b FAILED: assert 1==2\n2 passed, 1 failed"
        ok, _ = filt.should_ingest(content, "tool", "tool_output")
        assert ok

    def test_normalize_preserves_content(self, filt):
        content = "line1\nline2\nline3\n"
        result = filt.normalize(content)
        assert "line1" in result
        assert "line2" in result

    def test_whitespace_only_tool_output_still_ingested(self, filt):
        ok, _ = filt.should_ingest("   ", "tool", "tool_output")
        assert ok, "Whitespace-only tool output should pass through"
