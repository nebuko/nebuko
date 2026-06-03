#!/usr/bin/env python3
"""Tests for split_message function in agent_script.py"""

import sys
import os
import unittest

# Import the module under test
sys.path.insert(0, os.path.dirname(__file__))
from agent_script import (
    split_message,
    _split_at_boundaries,
    _split_at_sentence_boundaries,
)


class TestSplitMessageBasic(unittest.TestCase):
    """Test basic split_message functionality."""

    def test_short_message_no_split(self):
        """Messages under max_length should not be split."""
        message = "This is a short message."
        result = split_message(message, max_length=10000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], message)

    def test_exact_length_message(self):
        """Messages exactly at max_length should not be split."""
        message = "x" * 100
        result = split_message(message, max_length=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], message)

    def test_empty_message(self):
        """Empty string should return empty list."""
        result = split_message("", max_length=100)
        self.assertEqual(result, [])

    def test_none_message(self):
        """None should return empty list."""
        result = split_message(None, max_length=100)
        self.assertEqual(result, [])

    def test_custom_max_length(self):
        """Custom max_length should be respected."""
        message = "Part one. Part two."
        result = split_message(message, max_length=10)
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 10)


class TestSplitAtParagraphBoundaries(unittest.TestCase):
    """Test splitting at paragraph boundaries (\n\n)."""

    def test_two_paragraphs_split(self):
        """Two long paragraphs should be split."""
        para1 = "A" * 6000
        para2 = "B" * 6000
        message = f"{para1}\n\n{para2}"
        result = split_message(message, max_length=10000)
        self.assertEqual(len(result), 2)
        self.assertLessEqual(len(result[0]), 10000)
        self.assertLessEqual(len(result[1]), 10000)

    def test_many_paragraphs_split(self):
        """Many paragraphs should be packed greedily."""
        paragraphs = [f"Paragraph {i}: " + "X" * 2000 for i in range(10)]
        message = "\n\n".join(paragraphs)
        result = split_message(message, max_length=10000)
        
        # Should be split into multiple parts
        self.assertGreater(len(result), 1)
        
        # Each part should be under max_length
        for part in result:
            self.assertLessEqual(len(part), 10000)
        
        # Reconstructed message should preserve content (minus \n\n separators)
        reconstructed = "\n\n".join(result)
        # Original paragraphs should all be present
        for i in range(10):
            self.assertIn(f"Paragraph {i}:", reconstructed)

    def test_single_very_long_paragraph(self):
        """A single paragraph longer than max_length falls back to line splitting."""
        para = "A" * 20000
        result = split_message(para, max_length=10000)
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 10000)


class TestSplitAtLineBoundaries(unittest.TestCase):
    """Test splitting at line boundaries (\n)."""

    def test_long_lines_split(self):
        """Long lines without paragraph breaks should be split at newlines."""
        lines = [f"Line {i}: " + "Y" * 1500 for i in range(10)]
        message = "\n".join(lines)
        result = split_message(message, max_length=10000)
        
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 10000)

    def test_single_very_long_line(self):
        """A single very long line falls back to hard split."""
        line = "Z" * 20000
        result = split_message(line, max_length=10000)
        self.assertEqual(len(result), 2)
        self.assertLessEqual(len(result[0]), 10000)
        self.assertLessEqual(len(result[1]), 10000)


class TestSplitAtSentenceBoundaries(unittest.TestCase):
    """Test splitting at sentence boundaries."""

    def test_sentences_split(self):
        """Long text without newlines should be split at sentence boundaries."""
        sentences = [f"This is sentence number {i}." for i in range(50)]
        message = " ".join(sentences)
        result = split_message(message, max_length=500)
        
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 500)

    def test_exclamation_and_question_marks(self):
        """Splitting should work with ! and ? sentence terminators."""
        text = "Hello! How are you? I am fine! Thanks. " * 50
        result = split_message(text, max_length=500)
        
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 500)


class TestHardSplitFallback(unittest.TestCase):
    """Test hard split fallback when no natural boundaries exist."""

    def test_no_spaces_no_newlines(self):
        """Text with no spaces or newlines should be hard-split."""
        message = "A" * 25000
        result = split_message(message, max_length=10000)
        self.assertEqual(len(result), 3)
        for part in result:
            self.assertLessEqual(len(part), 10000)

    def test_single_word_too_long(self):
        """A single word longer than max_length should be hard-split."""
        message = "X" * 15000
        result = split_message(message, max_length=5000)
        self.assertEqual(len(result), 3)
        for part in result:
            self.assertLessEqual(len(part), 5000)


class TestSplitAtBoundaries(unittest.TestCase):
    """Test the _split_at_boundaries helper function."""

    def test_returns_none_for_short_text(self):
        """Should return None if text is already under max_length."""
        result = _split_at_boundaries("short text", 100, "\n\n")
        self.assertIsNone(result)

    def test_splits_at_delimiter(self):
        """Should split at the specified delimiter."""
        text = "part1\n\npart2\n\npart3"
        result = _split_at_boundaries(text, 10, "\n\n")
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 10)

    def test_first_segment_too_long(self):
        """Should return None if first segment exceeds max_length."""
        text = "A" * 100 + "\n\n" + "B" * 10
        result = _split_at_boundaries(text, 50, "\n\n")
        self.assertIsNone(result)


class TestSplitAtSentenceBoundaries(unittest.TestCase):
    """Test the _split_at_sentence_boundaries helper function."""

    def test_returns_none_for_short_text(self):
        """Should return None if text is already under max_length."""
        result = _split_at_sentence_boundaries("short text", 100)
        self.assertIsNone(result)

    def test_finds_last_sentence_boundary(self):
        """Should find the last sentence boundary before max_length."""
        text = "First sentence. Second sentence. Third sentence. " * 20
        result = _split_at_sentence_boundaries(text, 200)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 200)
        # Should end with sentence punctuation
        self.assertTrue(result[-1] in ".!?")

    def test_no_sentence_boundaries(self):
        """Should return None if no sentence boundaries found."""
        text = "No periods here at all " * 20
        result = _split_at_sentence_boundaries(text, 100)
        self.assertIsNone(result)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and corner scenarios."""

    def test_message_with_only_newlines(self):
        """Message with only newlines should be handled gracefully."""
        message = "\n\n\n\n"
        result = split_message(message, max_length=100)
        # Should not crash, may return empty or single-part
        self.assertIsInstance(result, list)

    def test_message_with_trailing_newlines(self):
        """Message with trailing newlines should preserve them."""
        message = "Content\n\n"
        result = split_message(message, max_length=100)
        self.assertEqual(len(result), 1)

    def test_multiple_consecutive_newlines(self):
        """Multiple consecutive newlines should be handled."""
        message = "Part1\n\n\n\nPart2"
        result = split_message(message, max_length=10)
        # Should not crash
        self.assertIsInstance(result, list)

    def test_unicode_content(self):
        """Unicode characters should be handled correctly."""
        message = "Café ☕\n\n日本語 🇯🇵\n\nEmoji 🎉🎊"
        result = split_message(message, max_length=100)
        self.assertEqual(len(result), 1)

    def test_unicode_long_message(self):
        """Long unicode message should split correctly."""
        para1 = "é" * 6000
        para2 = "日" * 6000
        message = f"{para1}\n\n{para2}"
        result = split_message(message, max_length=10000)
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 10000)

    def test_mixed_content(self):
        """Mixed content (paragraphs, lines, sentences) should work."""
        parts = [
            "First paragraph.\n\nSecond paragraph.",
            "Line 1\nLine 2\nLine 3",
            "Sentence one. Sentence two. Sentence three.",
        ]
        message = "\n\n".join(parts) * 50  # Repeat to make it long
        result = split_message(message, max_length=3000)
        
        self.assertGreater(len(result), 1)
        for part in result:
            self.assertLessEqual(len(part), 3000)


class TestContentPreservation(unittest.TestCase):
    """Test that content is preserved across splits."""

    def test_all_content_present(self):
        """All original content should be present in the parts."""
        original = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        result = split_message(original, max_length=30)
        reconstructed = "".join(result)
        self.assertIn("Paragraph one", reconstructed)
        self.assertIn("Paragraph two", reconstructed)
        self.assertIn("Paragraph three", reconstructed)

    def test_no_data_loss_on_long_message(self):
        """No data should be lost when splitting long messages."""
        paragraphs = [f"Section {i}: " + "Data " * 100 for i in range(20)]
        original = "\n\n".join(paragraphs)
        result = split_message(original, max_length=5000)
        reconstructed = "".join(result)
        
        # All section headers should be present
        for i in range(20):
            self.assertIn(f"Section {i}:", reconstructed)


if __name__ == "__main__":
    unittest.main()
