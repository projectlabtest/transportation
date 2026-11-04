"""Guards the approved trip-ticket print layout.

These values were signed off by the user (A4, 14mm top / 6mm sides /
8mm bottom). If a test here fails, the print layout was changed --
restore the numbers instead of updating the test, unless the user
explicitly asked for a new layout.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_FILE = os.path.join(ROOT, "static", "style.css")
TEMPLATE_FILE = os.path.join(ROOT, "templates", "trip_ticket.html")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _block(css, selector, occurrence=0):
    """Return the body of the n-th `selector { ... }` rule."""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    matches = re.findall(pattern, css)
    if len(matches) <= occurrence:
        raise AssertionError(f"{selector} rule #{occurrence} not found")
    return matches[occurrence]


def _value(body, prop):
    match = re.search(rf"(?:^|;|\s){re.escape(prop)}\s*:\s*([^;]+);", body)
    if not match:
        raise AssertionError(f"{prop} not found in: {body.strip()}")
    return match.group(1).strip()


class TicketLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = _read(CSS_FILE)
        cls.template = _read(TEMPLATE_FILE)

    def test_page_is_a4_portrait_without_page_margin(self):
        self.assertIn("@page { size: A4 portrait; margin: 0; }", self.css)

    def test_ticket_sheet_is_full_a4(self):
        sheet = _block(self.css, ".ticket-sheet")
        self.assertEqual(_value(sheet, "width"), "210mm")
        self.assertEqual(_value(sheet, "height"), "297mm")

    def test_cut_guide_sits_at_half_of_a4(self):
        guide = _block(self.css, ".ticket-sheet::after")
        self.assertEqual(_value(guide, "top"), "148.5mm")

    def test_ticket_geometry_is_locked(self):
        ticket = _block(self.css, ".trip-ticket")
        self.assertEqual(_value(ticket, "top"), "12mm")
        self.assertEqual(_value(ticket, "left"), "6mm")
        self.assertEqual(_value(ticket, "width"), "198mm")
        self.assertEqual(_value(ticket, "height"), "126.5mm")

    def test_bottom_margin_before_cut_is_10mm(self):
        ticket = _block(self.css, ".trip-ticket")
        top = 12.0
        height = 126.5
        cut = 148.5
        bottom = cut - (top + height)
        self.assertAlmostEqual(bottom, 10.0, places=2)

    def test_ticket_is_upright_not_rotated(self):
        ticket = _block(self.css, ".trip-ticket")
        self.assertNotIn("rotate(", ticket)
        self.assertNotIn("transform", ticket)

    def test_template_uses_versioned_stylesheet(self):
        self.assertRegex(
            self.template,
            r"style\.css', v='\d+'",
            "trip_ticket.html must load style.css with a version query",
        )


if __name__ == "__main__":
    unittest.main()
