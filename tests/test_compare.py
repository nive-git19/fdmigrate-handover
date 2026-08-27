"""reconcile._compare_ticket - the field-level verifier (D3). A clean match must
survive banner/marker/case noise; real mismatches must be caught; by-design gaps
(unmapped agent/group, strict-dropped CF) must NOT be flagged."""
import unittest

from fdmigrate.config import Config
from fdmigrate.reconcile import _compare_ticket


def cfg(**overrides):
    data = {"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
            "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}}
    data.update(overrides)
    return Config(data)


class TestCompareTicket(unittest.TestCase):
    def test_clean_match_tolerates_banner_marker_and_case(self):
        s = {"subject": "Hello", "description_text": "Body text", "status": 2,
             "priority": 1, "source": 1, "type": "Question",
             "requester": {"email": "A@X.com"}, "tags": ["vip"],
             "responder_id": 7, "group_id": 8, "custom_fields": {"cf": "1"}}
        t = {"subject": "Hello",
             "description_text": "Body text <hr>[Migrated record] Originally created...",
             "status": 2, "priority": 1, "source": 1, "type": "Question",
             "requester": {"email": "a@x.com"},                # case differs -> OK
             "tags": ["vip", "fd-migration-500"],              # marker added -> OK
             "responder_id": 70, "group_id": 80, "custom_fields": {"cf_tgt": "1"}}
        bad = _compare_ticket(s, t, cfg(), {"7": "70"}, {"8": "80"}, {"cf": "cf_tgt"})
        self.assertEqual(bad, [])

    def test_detects_real_mismatches(self):
        s = {"subject": "A", "description_text": "one", "status": 2, "priority": 1,
             "source": 1, "type": "Question", "requester": {"email": "a@x.com"},
             "tags": ["vip"], "responder_id": 7, "custom_fields": {"cf": "1"}}
        t = {"subject": "A", "description_text": "one", "status": 3, "priority": 1,
             "source": 1, "type": "Question", "requester": {"email": "b@x.com"},
             "tags": ["fd-migration-1"], "responder_id": 99, "custom_fields": {"cf_tgt": "2"}}
        bad = _compare_ticket(s, t, cfg(), {"7": "70"}, {}, {"cf": "cf_tgt"})
        self.assertIn("status", bad)              # 2 vs 3
        self.assertIn("requester_email", bad)     # a vs b
        self.assertIn("responder", bad)           # want 70, got 99
        self.assertIn("tags", bad)                # source 'vip' missing on target
        self.assertIn("cf:cf", bad)               # value 1 vs 2

    def test_by_design_gaps_not_flagged(self):
        # unmapped agent/group (not in maps) and strict-dropped CF (not in name map)
        s = {"subject": "A", "description_text": "one", "status": 2, "priority": 1,
             "source": 1, "type": "Question", "requester": {"email": "a@x.com"},
             "tags": ["vip"], "responder_id": 7, "group_id": 8,
             "custom_fields": {"dropped": "x"}}
        t = {"subject": "A", "description_text": "one", "status": 2, "priority": 1,
             "source": 1, "type": "Question", "requester": {"email": "a@x.com"},
             "tags": ["vip", "fd-migration-1"], "responder_id": None,
             "group_id": None, "custom_fields": {}}
        bad = _compare_ticket(s, t, cfg(), {}, {}, {})   # empty maps -> gaps are by-design
        self.assertEqual(bad, [])

    def test_status_map_applied_in_comparison(self):
        s = {"subject": "A", "description_text": "x", "status": 2, "priority": 1,
             "source": 1, "type": "Q", "requester": {"email": "a@x.com"}, "tags": []}
        t = {"subject": "A", "description_text": "x", "status": 4, "priority": 1,
             "source": 1, "type": "Q", "requester": {"email": "a@x.com"},
             "tags": ["fd-migration-1"]}
        # with map 2->4, the differing status ids are considered a MATCH
        bad = _compare_ticket(s, t, cfg(mapping={"status": {2: 4}}), {}, {}, {})
        self.assertNotIn("status", bad)


if __name__ == "__main__":
    unittest.main()
