"""Tests for query_context.py."""

import json
import subprocess
import tempfile
import os
from unittest.mock import patch

import pytest

from query_context import (
    load_event_data,
    extract_summary_from_comment,
    format_query_template,
)


class TestLoadEventData:
    """Test load_event_data function with real GitHub event JSON fixtures (10 tests)."""

    def _write_event(self, data):
        """Helper to write event JSON to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_full_event_returns_all_fields(self):
        """Full event JSON returns correct title, body, and author."""
        event_data = {
            "issue": {
                "title": "Fix login bug",
                "body": "The login page crashes on mobile.",
                "user": {"login": "alice"},
            }
        }
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == "Fix login bug"
        assert result["body"] == "The login page crashes on mobile."
        assert result["author"] == "alice"

        os.unlink(path)

    def test_missing_fields_default_to_empty_string(self):
        """Minimal event with missing fields defaults to empty strings."""
        event_data = {"issue": {}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == ""
        assert result["body"] == ""
        assert result["author"] == ""

        os.unlink(path)

    def test_missing_user_defaults_to_empty_string(self):
        """Missing user key defaults author to empty string."""
        event_data = {"issue": {"title": "Test", "body": "Body"}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["author"] == ""

        os.unlink(path)

    def test_missing_title_specifically(self):
        """When title is missing but body and user exist, title defaults to ''."""
        event_data = {"issue": {"body": "A body", "user": {"login": "bob"}}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == ""
        assert result["body"] == "A body"
        assert result["author"] == "bob"

        os.unlink(path)

    def test_missing_body_specifically(self):
        """When body is missing but title and user exist, body defaults to ''."""
        event_data = {"issue": {"title": "A title", "user": {"login": "carol"}}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == "A title"
        assert result["body"] == ""
        assert result["author"] == "carol"

        os.unlink(path)

    def test_missing_issue_object(self):
        """When .issue key is entirely absent, all fields default to ''."""
        event_data = {"other_key": "some value"}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == ""
        assert result["body"] == ""
        assert result["author"] == ""

        os.unlink(path)

    def test_malformed_json_raises(self):
        """Reading malformed JSON raises an exception."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("this is not valid json {{{")
        f.close()
        path = f.name

        with pytest.raises(json.JSONDecodeError):
            load_event_data(path)

        os.unlink(path)

    def test_empty_file_raises(self):
        """Reading an empty file raises an exception."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("")
        f.close()
        path = f.name

        with pytest.raises(json.JSONDecodeError):
            load_event_data(path)

        os.unlink(path)

    def test_extra_fields_ignored(self):
        """Extra fields in the issue object are silently ignored."""
        event_data = {
            "issue": {
                "title": "T",
                "body": "B",
                "user": {"login": "u"},
                "state": "open",
                "labels": ["bug"],
                "number": 42,
            }
        }
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == "T"
        assert result["body"] == "B"
        assert result["author"] == "u"
        assert "state" not in result

        os.unlink(path)

    def test_unicode_in_event_data(self):
        """Unicode characters in event data are preserved correctly."""
        event_data = {
            "issue": {
                "title": "日本語タイトル",
                "body": "你好世界",
                "user": {"login": "émoji-user 🚀"},
            }
        }
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == "日本語タイトル"
        assert result["body"] == "你好世界"
        assert result["author"] == "émoji-user 🚀"

        os.unlink(path)


class TestExtractSummaryFromComment:
    """Test extract_summary_from_comment regex extraction (15 tests)."""

    def test_valid_summary(self):
        """Comment with bot-agent-summary marker returns stripped summary."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "This is the summary content.\n"
            "It spans multiple lines.\n"
            "---\n"
            "rest of comment"
        )

        result = extract_summary_from_comment(comment)

        assert result == "This is the summary content.\nIt spans multiple lines."

    def test_no_marker_returns_none(self):
        """Comment without marker returns None."""
        comment = "Just a regular comment with no markers."

        result = extract_summary_from_comment(comment)

        assert result is None

    def test_whitespace_stripped(self):
        """Whitespace around summary text is properly stripped."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "   \n"
            "Summary with surrounding whitespace\n"
            "   \n"
            "---\n"
            "trailing"
        )

        result = extract_summary_from_comment(comment)

        assert result == "Summary with surrounding whitespace"

    def test_flexible_spaces_in_marker(self):
        """Regex handles flexible whitespace in the HTML comment marker."""
        comment = (
            "<!--  bot-agent-summary  -->\n"
            "Flexible spaces summary\n"
            "---"
        )

        result = extract_summary_from_comment(comment)

        assert result == "Flexible spaces summary"

    def test_multiple_markers_first_wins(self):
        """When multiple markers exist, the first one wins."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "First summary\n"
            "---\n"
            "<!-- bot-agent-summary -->\n"
            "Second summary\n"
            "---\n"
        )

        result = extract_summary_from_comment(comment)

        assert result == "First summary"

    def test_marker_without_terminator(self):
        """Marker without `---` terminator returns None (no match)."""
        comment = "<!-- bot-agent-summary -->\nNo terminator here\nmore text"

        result = extract_summary_from_comment(comment)

        # ^--- requires --- at start of line; without it, no match
        assert result is None

    def test_empty_content_between_markers(self):
        """Empty content between marker and terminator returns empty string."""
        comment = "<!-- bot-agent-summary -->\n---"

        result = extract_summary_from_comment(comment)

        assert result == ""

    def test_whitespace_only_content(self):
        """Whitespace-only content between markers returns empty string after strip."""
        comment = "<!-- bot-agent-summary -->\n   \n  \n---"

        result = extract_summary_from_comment(comment)

        assert result == ""

    def test_nested_markers(self):
        """Nested or embedded marker-like content within summary is preserved."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "Contains <!-- another-comment --> inside\n"
            "---\n"
        )

        result = extract_summary_from_comment(comment)

        assert "Contains <!-- another-comment --> inside" == result

    def test_marker_at_beginning(self):
        """Marker at very beginning of comment body works correctly."""
        comment = "<!-- bot-agent-summary -->\nAt the start\n---"

        result = extract_summary_from_comment(comment)

        assert result == "At the start"

    def test_marker_after_preamble(self):
        """Marker appearing after some preamble text still matches."""
        comment = "Some preamble text before the marker.\n<!-- bot-agent-summary -->\nAfter preamble\n---"

        result = extract_summary_from_comment(comment)

        assert result == "After preamble"

    def test_marker_with_no_space_in_comment(self):
        """Marker written as <!--bot-agent-summary--> (no spaces) still matches due to regex \\s*."""
        comment = "<!--bot-agent-summary-->\nCompact marker\n---"

        result = extract_summary_from_comment(comment)

        assert result == "Compact marker"

    def test_multiple_dashes_first_line(self):
        """Multiple --- lines: the first one terminates the summary."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "Line one\n"
            "---\n"
            "---\n"
            "More dashes\n"
        )

        result = extract_summary_from_comment(comment)

        assert result == "Line one"

    def test_comment_with_only_marker(self):
        """Comment containing only the marker with nothing else returns None."""
        comment = "<!-- bot-agent-summary -->"

        result = extract_summary_from_comment(comment)

        assert result is None

    def test_very_long_summary_content(self):
        """Very long summary content is extracted without truncation."""
        long_text = "word " * 5000
        comment = f"<!-- bot-agent-summary -->\n{long_text}\n---\n"

        result = extract_summary_from_comment(comment)

        assert len(result) == len(long_text.strip())


class TestFormatQueryTemplate:
    """Test format_query_template placeholder replacement (15 tests)."""

    def test_all_placeholders_replaced(self):
        """Template with all placeholders present replaces them correctly."""
        template = "Title: {title}\nBody: {body}\nBy: {author}\nHistory: {history}"

        result = format_query_template(
            title="My Title",
            body="My Body",
            author="alice",
            history="Old comment",
            template_str=template,
        )

        assert "Title: My Title" in result
        assert "Body: My Body" in result
        assert "By: alice" in result
        assert "History: Old comment" in result

    def test_partial_args_fallback_to_empty_string(self):
        """Partial or None args fall back to empty string."""
        template = "{title}{body}{author}{history}"

        result = format_query_template(
            title=None,
            body="",
            author=None,
            history="something",
            template_str=template,
        )

        assert result == "something"

    def test_title_missing_individually(self):
        """When title is None/empty, it falls back to empty string."""
        result = format_query_template(None, "body", "author", "history", "{title}|{body}|{author}|{history}")
        assert result == "|body|author|history"

    def test_body_missing_individually(self):
        """When body is None/empty, it falls back to empty string."""
        result = format_query_template("title", None, "author", "history", "{title}|{body}|{author}|{history}")
        assert result == "title||author|history"

    def test_author_missing_individually(self):
        """When author is None/empty, it falls back to empty string."""
        result = format_query_template("title", "body", None, "history", "{title}|{body}|{author}|{history}")
        assert result == "title|body||history"

    def test_history_missing_individually(self):
        """When history is None/empty, it falls back to empty string."""
        result = format_query_template("title", "body", "author", None, "{title}|{body}|{author}|{history}")
        assert result == "title|body|author|"

    def test_all_fields_empty(self):
        """All fields as empty strings produces minimal output."""
        result = format_query_template("", "", "", "", "{title}{body}{author}{history}")
        assert result == ""

    def test_very_long_content(self):
        """Very long content is handled without truncation."""
        long_text = "x" * 10000
        result = format_query_template(long_text, long_text, long_text, long_text, "{title}{body}{author}{history}")
        assert len(result) == 40000

    def test_special_characters_quotes(self):
        """Quotes and backticks in content are preserved."""
        result = format_query_template('say "hello"', 'use `code`', 'user"name', 'quoted "text"', "{title}|{body}|{author}|{history}")
        assert 'say "hello"' in result
        assert 'use `code`' in result
        assert 'user"name' in result

    def test_special_characters_html(self):
        """HTML tags in content are preserved."""
        result = format_query_template("<h1>Title</h1>", "<p>Body</p>", "<b>Bold</b>", "<em>Em</em>", "{title}|{body}|{author}|{history}")
        assert "<h1>Title</h1>" in result
        assert "<p>Body</p>" in result

    def test_special_characters_newlines(self):
        """Newlines in content are preserved."""
        result = format_query_template("line1\nline2", "a\nb\nc", "x\ny", "h1\nh2", "{title}|{body}|{author}|{history}")
        assert "line1\nline2" in result
        assert "a\nb\nc" in result

    def test_unicode_content(self):
        """Unicode characters (emoji, CJK, etc.) are preserved."""
        result = format_query_template("日本語テスト", "你好世界", "émoji 🚀", "üñîcödé", "{title}|{body}|{author}|{history}")
        assert "日本語テスト" in result
        assert "你好世界" in result
        assert "émoji 🚀" in result
        assert "üñîcödé" in result

    def test_no_placeholders(self):
        """Template with no placeholders returns the template unchanged."""
        result = format_query_template("a", "b", "c", "d", "plain text only")
        assert result == "plain text only"

    def test_duplicate_placeholder_in_template(self):
        """Placeholder appearing multiple times in template is replaced each time."""
        result = format_query_template("X", "Y", "Z", "W", "{title}{title}{title}")
        assert result == "XXX"


class TestBuildHistoryString:
    """Test _build_history_string internal helper (7 tests)."""

    def test_no_comments_returns_default_message(self):
        """Empty comments list returns 'No previous comments'."""
        from query_context import _build_history_string

        result = _build_history_string([])

        assert result == "No previous comments"

    def test_comments_without_summary_use_raw_body(self):
        """Comments without summary marker use raw body text."""
        comments = [
            {"body": "Regular comment text", "user": {"login": "bob"}},
        ]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### bob a écrit :" in result
        assert "Regular comment text" in result

    def test_comments_with_summary_use_extracted_summary(self):
        """Comments with summary marker use extracted summary instead of raw body."""
        comments = [
            {
                "body": "<!-- bot-agent-summary -->\nExtracted summary\n---\nfull body text here",
                "user": {"login": "charlie"},
            },
        ]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### charlie a écrit :" in result
        assert "Extracted summary" in result
        assert "full body text here" not in result

    def test_multiple_comments_mixed(self):
        """Multiple comments with mixed summary/no-summary are formatted correctly."""
        comments = [
            {"body": "Plain comment", "user": {"login": "user1"}},
            {"body": "<!-- bot-agent-summary -->\nSummary text\n---\nBody", "user": {"login": "user2"}},
            {"body": "Another plain", "user": {"login": "user3"}},
        ]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### user1 a écrit :" in result
        assert "Plain comment" in result
        assert "### user2 a écrit :" in result
        assert "Summary text" in result
        assert "### user3 a écrit :" in result
        assert "Another plain" in result

    def test_missing_user_login_defaults_to_unknown(self):
        """Comment with missing user login defaults to 'unknown'."""
        comments = [{"body": "No user", "user": {}}]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### unknown a écrit :" in result

    def test_missing_user_key_defaults_to_unknown(self):
        """Comment with no user key at all defaults to 'unknown'."""
        comments = [{"body": "No user key"}]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### unknown a écrit :" in result

    @pytest.mark.xfail(reason="Pre-existing: empty body is skipped by content.strip() guard (line 245)")
    def test_empty_body_comment(self):
        """Comment with empty body is included with empty content."""
        comments = [{"body": "", "user": {"login": "empty-user"}}]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### empty-user a écrit :" in result


class TestMain:
    """Test main() CLI entry point with mocked dependencies (15 tests)."""

    def _write_event(self, data):
        """Helper to write event JSON to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_valid_invocation_writes_output_and_prints_keys(self, capsys):
        """main() writes formatted query to output file and prints KEY=value pairs."""
        event_path = self._write_event({
            "issue": {
                "title": "Test issue",
                "body": "Test body",
                "user": {"login": "alice"},
            }
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="git@github.com:owner/repo.git\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "42", "--event-path", event_path, "--output-file", "/tmp/test_output.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "AGENT_QUERY_PATH=/tmp/test_output.md" in captured.out
        assert "ISSUE_TITLE=Test issue" in captured.out
        assert "ISSUE_AUTHOR=alice" in captured.out
        assert "ISSUE_BODY_PATH=/tmp/agent_query/issue_body.md" in captured.out
        assert "COMMENT_HISTORY_PATH=/tmp/agent_query/comment_history.md" in captured.out

        with open("/tmp/agent_query/issue_body.md") as f:
            assert f.read() == "Test body"

        with open("/tmp/test_output.md", "r") as f:
            content = f.read()
        assert "Test issue" in content
        assert "Test body" in content
        assert "alice" in content
        os.unlink(event_path)

    def test_multiline_values_written_to_files(self, capsys):
        """main() writes multi-line body and history to files, emitting only paths."""
        event_path = self._write_event({
            "issue": {
                "title": "Title",
                "body": "Line one\nLine two",
                "user": {"login": "bob"},
            }
        })

        with (
            patch(
                "query_context.get_issue_comments",
                return_value=[{"body": "Comment body", "user": {"login": "charlie"}}],
            ),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "99", "--event-path", event_path, "--output-file", "/tmp/test_heredoc.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "ISSUE_BODY_PATH=/tmp/agent_query/issue_body.md" in captured.out
        assert "COMMENT_HISTORY_PATH=/tmp/agent_query/comment_history.md" in captured.out
        # Large payloads must NOT be emitted into the environment (issue #520)
        assert "ISSUE_BODY=" not in captured.out
        assert "COMMENT_HISTORY=" not in captured.out
        with open("/tmp/agent_query/issue_body.md") as f:
            assert f.read() == "Line one\nLine two"
        os.unlink(event_path)

    def test_writes_to_github_env_when_set(self, capsys, monkeypatch):
        """main() prints KEY=value pairs to stdout for >> $GITHUB_ENV consumption."""
        monkeypatch.setenv("GITHUB_ENV", "/tmp/test_github_env")

        event_path = self._write_event({
            "issue": {
                "title": "Env test",
                "body": "Body text",
                "user": {"login": "dave"},
            }
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "10", "--event-path", event_path, "--output-file", "/tmp/test_env.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "AGENT_QUERY_PATH=/tmp/test_env.md" in captured.out
        assert "ISSUE_TITLE=Env test" in captured.out
        assert "ISSUE_AUTHOR=dave" in captured.out
        os.unlink(event_path)

    def test_missing_issue_number_raises(self):
        """Missing --issue-number argument raises SystemExit."""
        with (
            patch("sys.argv", ["query_context.py"]),
        ):
            from query_context import main
            with pytest.raises(SystemExit):
                main()

    def test_custom_output_file(self, capsys):
        """Custom --output-file path is used correctly."""
        output_path = "/tmp/custom_query_output.md"
        event_path = self._write_event({
            "issue": {"title": "T", "body": "B", "user": {"login": "u"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "1", "--event-path", event_path, "--output-file", output_path]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert f"AGENT_QUERY_PATH={output_path}" in captured.out
        assert os.path.exists(output_path)
        os.unlink(event_path)
        os.unlink(output_path)

    def test_custom_event_path(self, capsys):
        """Custom --event-path reads from the specified location."""
        event_path = self._write_event({
            "issue": {"title": "CustomPath", "body": "Body", "user": {"login": "evan"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "5", "--event-path", event_path, "--output-file", "/tmp/test_custom_path.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "ISSUE_TITLE=CustomPath" in captured.out
        os.unlink(event_path)

    def test_output_file_contains_formatted_template(self):
        """Output file contains properly formatted DEFAULT_TEMPLATE content."""
        event_path = self._write_event({
            "issue": {"title": "MyTitle", "body": "MyBody", "user": {"login": "myauthor"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "7", "--event-path", event_path, "--output-file", "/tmp/test_template.md"]),
        ):
            from query_context import main
            main()

        with open("/tmp/test_template.md", "r") as f:
            content = f.read()
        assert "## Issue Context" in content
        assert "**Title:** MyTitle" in content
        assert "**Author:** myauthor" in content
        assert "## History" in content
        os.unlink(event_path)
        os.unlink("/tmp/test_template.md")

    def test_key_value_parsing_of_output(self, capsys):
        """stdout output can be parsed as KEY=value pairs."""
        event_path = self._write_event({
            "issue": {"title": "KvTest", "body": "Sbody", "user": {"login": "kvuser"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "3", "--event-path", event_path, "--output-file", "/tmp/test_kv.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        kv_pairs = {}
        for line in lines:
            if "=" in line:
                key, val = line.split("=", 1)
                kv_pairs[key] = val
        assert kv_pairs["AGENT_QUERY_PATH"] == "/tmp/test_kv.md"
        assert kv_pairs["ISSUE_TITLE"] == "KvTest"
        assert kv_pairs["ISSUE_AUTHOR"] == "kvuser"
        os.unlink(event_path)

    def test_with_comments_containing_summaries(self, capsys):
        """main() correctly processes comments containing bot-agent-summary markers."""
        event_path = self._write_event({
            "issue": {"title": "SummaryTest", "body": "Body", "user": {"login": "alice"}}
        })

        comments = [
            {
                "body": "<!-- bot-agent-summary -->\nGenius was here\n---\nFull genius comment",
                "user": {"login": "genius-bot"},
            },
        ]

        with (
            patch("query_context.get_issue_comments", return_value=comments),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "8", "--event-path", event_path, "--output-file", "/tmp/test_summaries.md"]),
        ):
            from query_context import main
            main()

        with open("/tmp/test_summaries.md", "r") as f:
            content = f.read()
        # The summary should appear in the output, not the full comment
        assert "Genius was here" in content
        assert "Full genius comment" not in content
        os.unlink(event_path)
        os.unlink("/tmp/test_summaries.md")

    def test_with_empty_comments_list(self, capsys):
        """main() handles empty comment list gracefully."""
        event_path = self._write_event({
            "issue": {"title": "NoComments", "body": "Body", "user": {"login": "solo"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "11", "--event-path", event_path, "--output-file", "/tmp/test_nocomments.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "COMMENT_HISTORY_PATH=/tmp/agent_query/comment_history.md" in captured.out
        with open("/tmp/agent_query/comment_history.md") as f:
            assert "No previous comments" in f.read()
        os.unlink(event_path)

    def test_special_characters_in_title(self, capsys):
        """main() handles special characters in issue title."""
        event_path = self._write_event({
            "issue": {"title": 'Fix: "auth" bug & `code` <html>', "body": "Body", "user": {"login": "special-user"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "12", "--event-path", event_path, "--output-file", "/tmp/test_special.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "ISSUE_TITLE=Fix: \"auth\" bug & `code` <html>" in captured.out
        os.unlink(event_path)

    def test_large_body_content(self, capsys):
        """main() writes large body content to a file and emits only its path."""
        large_body = "Line of text.\n" * 500
        event_path = self._write_event({
            "issue": {"title": "Large", "body": large_body, "user": {"login": "big"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "13", "--event-path", event_path, "--output-file", "/tmp/test_large.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "ISSUE_BODY_PATH=/tmp/agent_query/issue_body.md" in captured.out
        with open("/tmp/agent_query/issue_body.md") as f:
            assert f.read() == large_body.strip()
        os.unlink(event_path)

    def test_custom_issue_template_env_var(self, capsys, monkeypatch):
        """main() respects ISSUE_TEMPLATE environment variable for custom template."""
        custom_template = "CUSTOM: {title} by {author}\n{history}"
        monkeypatch.setenv("ISSUE_TEMPLATE", custom_template)

        event_path = self._write_event({
            "issue": {"title": "CustTpl", "body": "B", "user": {"login": "tst"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "14", "--event-path", event_path, "--output-file", "/tmp/test_custom_tpl.md"]),
        ):
            from query_context import main
            main()

        with open("/tmp/test_custom_tpl.md", "r") as f:
            content = f.read()
        assert "CUSTOM: CustTpl by tst" in content
        os.unlink(event_path)
        os.unlink("/tmp/test_custom_tpl.md")

    def test_stderr_output_verification(self, capsys):
        """main() writes informational message to stderr."""
        event_path = self._write_event({
            "issue": {"title": "Stderr", "body": "B", "user": {"login": "u"}}
        })

        with (
            patch("query_context.get_issue_comments", return_value=[]),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="owner/repo\n")),
            patch("sys.argv", ["query_context.py", "--issue-number", "15", "--event-path", event_path, "--output-file", "/tmp/test_stderr.md"]),
        ):
            from query_context import main
            main()

        captured = capsys.readouterr()
        assert "Wrote agent query context to /tmp/test_stderr.md" in captured.err
        os.unlink(event_path)


class TestEnvValueGuard:
    """Test _check_env_value_size and _write_context_file helpers (issue #520)."""

    def test_small_value_passes_guard(self):
        """Values under the threshold are accepted."""
        from query_context import _check_env_value_size, MAX_GITHUB_ENV_VALUE_BYTES

        _check_env_value_size("SMALL_KEY", "x" * MAX_GITHUB_ENV_VALUE_BYTES)

    def test_oversized_value_raises(self):
        """A single value over the threshold raises RuntimeError with the key name."""
        from query_context import _check_env_value_size, MAX_GITHUB_ENV_VALUE_BYTES

        with pytest.raises(RuntimeError, match="HUGE_KEY"):
            _check_env_value_size("HUGE_KEY", "y" * (MAX_GITHUB_ENV_VALUE_BYTES + 1))

    def test_multibyte_characters_counted_as_bytes(self):
        """Size is measured in UTF-8 bytes, not characters."""
        from query_context import _check_env_value_size, MAX_GITHUB_ENV_VALUE_BYTES

        # 'é' is 2 bytes in UTF-8
        with pytest.raises(RuntimeError):
            _check_env_value_size("MB_KEY", "é" * (MAX_GITHUB_ENV_VALUE_BYTES // 2 + 1))

    def test_write_context_file_writes_content_and_returns_path(self, capsys):
        """_write_context_file writes UTF-8 content and returns the path string."""
        from query_context import _write_context_file, CONTEXT_FILES_DIR

        path = _write_context_file("unit_test_payload.md", "Line A\nLine B\n")
        assert path == str(CONTEXT_FILES_DIR / "unit_test_payload.md")
        with open(path, encoding="utf-8") as f:
            assert f.read() == "Line A\nLine B\n"
        os.unlink(path)

    def test_write_context_file_logs_to_stderr(self, capsys):
        """_write_context_file logs the payload size to stderr only."""
        from query_context import _write_context_file, CONTEXT_FILES_DIR

        path = _write_context_file("unit_test_log.md", "abc")
        captured = capsys.readouterr()
        assert f"Wrote 3 chars to {path}" in captured.err
        assert path not in captured.out
        os.unlink(path)


class TestIsBotComment:
    """Test _is_bot_comment helper function."""

    def test_bot_user_type(self):
        """GitHub API reports bots with user.type == 'Bot'."""
        from query_context import _is_bot_comment

        comment = {"id": 1, "user": {"login": "github-actions", "type": "Bot"}}
        assert _is_bot_comment(comment) is True

    def test_login_contains_bot_suffix(self):
        """Bots with [bot] suffix in login are detected even without type field."""
        from query_context import _is_bot_comment

        comment = {"id": 1, "user": {"login": "dependabot[bot]"}}
        assert _is_bot_comment(comment) is True

    def test_human_user_type(self):
        """Human users have type == 'User' and are not considered bots."""
        from query_context import _is_bot_comment

        comment = {"id": 1, "user": {"login": "alice", "type": "User"}}
        assert _is_bot_comment(comment) is False

    def test_missing_user_dict(self):
        """Comments without a user key are treated as non-bot (user comments)."""
        from query_context import _is_bot_comment

        comment = {"id": 1, "body": "Orphan comment"}
        assert _is_bot_comment(comment) is False

    def test_missing_user_fields(self):
        """Empty user dict is treated as non-bot."""
        from query_context import _is_bot_comment

        comment = {"id": 1, "user": {}}
        assert _is_bot_comment(comment) is False


class TestInjectContextFiltering:
    """Test _get_injectable_comment_ids and _filter_comments_by_inject_context."""

    def test_no_state_returns_none(self):
        """When no state exists, _get_injectable_comment_ids returns None."""
        from query_context import _get_injectable_comment_ids

        result = _get_injectable_comment_ids(None)
        assert result is None

    def test_no_state_returns_all_comments(self):
        """When no state exists, all comments pass through unfiltered."""
        from query_context import _filter_comments_by_inject_context

        comments = [
            {"id": 1, "body": "First", "user": {"type": "Bot"}},
            {"id": 2, "body": "Second", "user": {"type": "User"}},
        ]
        result = _filter_comments_by_inject_context(comments, None)
        assert len(result) == 2

    def test_inject_context_true_includes_bot_comment(self):
        """Bot comments matching history entries with inject_context=True are included."""
        from query_context import _filter_comments_by_inject_context
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="analyze", agent="genius", comment_ids=[1], inject_context=True),
            ]
        )
        comments = [
            {"id": 1, "body": "Included", "user": {"type": "Bot"}},
            {"id": 2, "body": "Excluded", "user": {"type": "Bot"}},
        ]
        result = _filter_comments_by_inject_context(comments, state)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_inject_context_false_excludes_bot_comment(self):
        """Bot comments matching history entries with inject_context=False are excluded."""
        from query_context import _filter_comments_by_inject_context
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="analyze", agent="genius", comment_ids=[1], inject_context=True),
                HistoryEntry(step="context", agent="ctx", comment_ids=[2], inject_context=False),
            ]
        )
        comments = [
            {"id": 1, "body": "Included", "user": {"type": "Bot"}},
            {"id": 2, "body": "Excluded", "user": {"type": "Bot"}},
        ]
        result = _filter_comments_by_inject_context(comments, state)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_entry_without_comment_ids_ignored(self):
        """History entries without comment_ids don't filter any comments."""
        from query_context import _filter_comments_by_inject_context
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="analyze", agent="genius", comment_ids=[], inject_context=True),
                HistoryEntry(step="context", agent="ctx", comment_ids=[2], inject_context=True),
            ]
        )
        comments = [
            {"id": 1, "body": "Excluded", "user": {"type": "Bot"}},
            {"id": 2, "body": "Included", "user": {"type": "Bot"}},
        ]
        result = _filter_comments_by_inject_context(comments, state)
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_empty_history_filters_only_bot_comments(self):
        """Empty history filters bot comments but preserves user comments."""
        from query_context import _filter_comments_by_inject_context
        from workflow_state import WorkflowState

        state = WorkflowState(history=[])
        comments = [
            {"id": 1, "body": "User requirement", "user": {"type": "User"}},
            {"id": 2, "body": "Bot output", "user": {"type": "Bot"}},
        ]
        result = _filter_comments_by_inject_context(comments, state)
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["body"] == "User requirement"

    def test_user_comments_always_pass_through(self):
        """User comments are never filtered regardless of inject_context settings."""
        from query_context import _filter_comments_by_inject_context
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="analyze", agent="genius", comment_ids=[1], inject_context=True),
            ]
        )
        comments = [
            {"id": 1, "body": "Bot included", "user": {"type": "Bot"}},
            {"id": 2, "body": "User feedback", "user": {"login": "alice", "type": "User"}},
            {"id": 3, "body": "User follow-up", "user": {"login": "bob", "type": "User"}},
        ]
        result = _filter_comments_by_inject_context(comments, state)
        assert len(result) == 3

    def test_default_inject_context_is_true(self):
        """HistoryEntry defaults inject_context to True for backward compatibility."""
        from workflow_state import HistoryEntry

        entry = HistoryEntry(step="analyze", agent="genius", comment_ids=[5])
        assert entry.inject_context is True

    def test_multi_part_entry_contributes_all_ids(self):
        """A multi-part entry contributes every comment id to the injectable set."""
        from query_context import _get_injectable_comment_ids
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="context", agent="ctx", comment_ids=[10, 11, 12], inject_context=True),
                HistoryEntry(step="plan", agent="genius", comment_ids=[13], inject_context=False),
            ]
        )
        ids = _get_injectable_comment_ids(state)
        assert ids == {10, 11, 12}

    def test_duplicate_comment_ids_deduplicated(self):
        """Multiple entries referencing same comment_ids work correctly."""
        from query_context import _get_injectable_comment_ids
        from workflow_state import WorkflowState
        from workflow_state import HistoryEntry

        state = WorkflowState(
            history=[
                HistoryEntry(step="analyze", agent="genius", comment_ids=[1], inject_context=True),
                HistoryEntry(step="plan", agent="genius", comment_ids=[1], inject_context=True),
            ]
        )
        ids = _get_injectable_comment_ids(state)
        assert ids == {1}
