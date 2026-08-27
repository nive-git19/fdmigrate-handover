"""Pure helper logic: base.strip_empty/remap_cf, reconcile._norm_text,
tickets._is_future/_matches_filters/_map_custom_fields/_marker_tag."""
import unittest

from fdmigrate.phases.base import strip_empty, remap_cf
from fdmigrate.reconcile import _norm_text
from fdmigrate.phases import tickets


class TestStripEmpty(unittest.TestCase):
    def test_drops_none_empty_str_and_list(self):
        out = strip_empty({"a": 1, "b": None, "c": "", "d": [], "e": 0, "f": False})
        # 0 and False are kept (not in the None/""/[] drop set)
        self.assertEqual(out, {"a": 1, "e": 0, "f": False})


class TestRemapCf(unittest.TestCase):
    def test_remaps_known_drops_unknown_and_null(self):
        vals = {"src_a": "x", "src_b": None, "src_c": "y"}
        name_map = {"src_a": "tgt_a", "src_c": "tgt_c"}  # src_b has no target
        self.assertEqual(remap_cf(vals, name_map), {"tgt_a": "x", "tgt_c": "y"})

    def test_empty_map_returns_empty(self):
        self.assertEqual(remap_cf({"a": 1}, {}), {})
        self.assertEqual(remap_cf(None, {"a": "b"}), {})


class TestNormText(unittest.TestCase):
    def test_strips_html_and_collapses_whitespace_and_lowers(self):
        self.assertEqual(_norm_text("<p>Hello   World</p>"), "hello world")
        self.assertEqual(_norm_text("A<br>B"), "a b")
        self.assertEqual(_norm_text(None), "")


class TestIsFuture(unittest.TestCase):
    def test_future_and_past(self):
        self.assertTrue(tickets._is_future("2999-01-01T00:00:00Z"))
        self.assertFalse(tickets._is_future("2000-01-01T00:00:00Z"))
        self.assertFalse(tickets._is_future(None))
        self.assertFalse(tickets._is_future("not-a-date"))


class TestMarkerTag(unittest.TestCase):
    def test_marker_shape(self):
        self.assertEqual(tickets._marker_tag(1234), "fd-migration-1234")


class TestMatchesFilters(unittest.TestCase):
    def test_empty_filter_matches_all(self):
        self.assertTrue(tickets._matches_filters({"status": 2}, {}))

    def test_status_and_tag_and_date_bounds(self):
        t = {"status": 2, "priority": 1, "tags": ["vip"], "created_at": "2021-06-01T00:00:00Z"}
        self.assertTrue(tickets._matches_filters(t, {"status": 2}))
        self.assertFalse(tickets._matches_filters(t, {"status": 5}))
        self.assertTrue(tickets._matches_filters(t, {"status": [2, 3]}))
        self.assertTrue(tickets._matches_filters(t, {"tags": ["VIP"]}))       # case-insensitive
        self.assertFalse(tickets._matches_filters(t, {"tags": ["other"]}))
        self.assertFalse(tickets._matches_filters(t, {"created_after": "2022-01-01T00:00:00Z"}))
        self.assertTrue(tickets._matches_filters(t, {"created_before": "2022-01-01T00:00:00Z"}))


class TestMapCustomFields(unittest.TestCase):
    def test_strict_drops_unmapped_keeps_mapped_skips_null_and_skip_marker(self):
        cf = {"a": "1", "b": "2", "c": None, "d": "4"}
        mapping = {"a": "tgt_a", "b": "__skip__"}
        # strict: only 'a' survives ('b' skipped, 'c' null, 'd' unmapped-dropped)
        self.assertEqual(tickets._map_custom_fields(cf, mapping, strict=True), {"tgt_a": "1"})

    def test_non_strict_passes_unmapped_through(self):
        cf = {"a": "1", "d": "4"}
        out = tickets._map_custom_fields(cf, {"a": "tgt_a"}, strict=False)
        self.assertEqual(out, {"tgt_a": "1", "d": "4"})


if __name__ == "__main__":
    unittest.main()
