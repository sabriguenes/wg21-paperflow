"""Tests for slide-deck detection in lib.pdf.__init__."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tomd.lib.pdf import _is_slide_deck, _is_standards_draft


def _mock_doc(pages):
    """Create a mock fitz document with given (width, height) page dimensions."""
    doc = MagicMock()
    doc.page_count = len(pages)
    mock_pages = []
    for w, h in pages:
        page = MagicMock()
        rect = MagicMock()
        rect.width = w
        rect.height = h
        page.rect = rect
        mock_pages.append(page)
    doc.__getitem__ = lambda self, i: mock_pages[i]
    return doc


class TestSlideDeckDetection:
    def test_landscape_small_is_slide_deck(self):
        doc = _mock_doc([(363, 272)] * 10)
        assert _is_slide_deck(doc) is True

    def test_portrait_letter_is_not_slide_deck(self):
        doc = _mock_doc([(612, 792)] * 10)
        assert _is_slide_deck(doc) is False

    def test_portrait_a4_is_not_slide_deck(self):
        doc = _mock_doc([(595, 842)] * 10)
        assert _is_slide_deck(doc) is False

    def test_landscape_a4_all_pages_is_slide_deck(self):
        """A4 landscape (842x595) exceeds the 600pt width cap of rule 1,
        but rule 2 catches it because every page is landscape."""
        doc = _mock_doc([(842, 595)] * 10)
        assert _is_slide_deck(doc) is True

    def test_landscape_1920x1080_is_slide_deck(self):
        """16:9 widescreen at native 1920x1080. Rule 1 fails on width,
        rule 2 succeeds because all pages are landscape."""
        doc = _mock_doc([(1920, 1080)] * 50)
        assert _is_slide_deck(doc) is True

    def test_mostly_landscape_wide_pages_is_not_slide_deck(self):
        """N5028 case: 105 landscape A4 pages + 9 portrait pages. Rule 1
        fails (width 842 >= 600), rule 2 fails (not 100% landscape).
        Stays out of the deck bucket so its high-quality conversion
        isn't lost to the early-exit."""
        pages = [(842, 595)] * 105 + [(595, 842)] * 9
        doc = _mock_doc(pages)
        assert _is_slide_deck(doc) is False

    def test_empty_document(self):
        doc = _mock_doc([])
        assert _is_slide_deck(doc) is False

    def test_mixed_below_threshold(self):
        """Less than 80% landscape small pages -> not a slide deck."""
        pages = [(363, 272)] * 7 + [(612, 792)] * 3
        doc = _mock_doc(pages)
        assert _is_slide_deck(doc) is False

    def test_mixed_above_threshold(self):
        """80%+ landscape small pages -> slide deck."""
        pages = [(363, 272)] * 9 + [(612, 792)] * 1
        doc = _mock_doc(pages)
        assert _is_slide_deck(doc) is True

    def test_single_landscape_page(self):
        doc = _mock_doc([(500, 375)])
        assert _is_slide_deck(doc) is True

    def test_widescreen_presentation(self):
        """16:9 widescreen at 540x405 (common Beamer)."""
        doc = _mock_doc([(540, 405)] * 20)
        assert _is_slide_deck(doc) is True


class TestStandardsDraftDetection:
    def test_large_document_is_standards_draft(self):
        doc = _mock_doc([(612, 792)] * 250)
        assert _is_standards_draft(doc) is True

    def test_exactly_200_pages_is_standards_draft(self):
        doc = _mock_doc([(612, 792)] * 200)
        assert _is_standards_draft(doc) is True

    def test_199_pages_is_not_standards_draft(self):
        doc = _mock_doc([(612, 792)] * 199)
        assert _is_standards_draft(doc) is False

    def test_small_document_is_not_standards_draft(self):
        doc = _mock_doc([(612, 792)] * 10)
        assert _is_standards_draft(doc) is False

    def test_empty_document(self):
        doc = _mock_doc([])
        assert _is_standards_draft(doc) is False
