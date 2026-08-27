"""D4 pre-flight coverage: _ticket_field_choices parsing + preflight_coverage gaps."""
import unittest

from fdmigrate.config import Config
from fdmigrate.reconcile import _ticket_field_choices, preflight_coverage
from tests._fakes import FakeClient, silent_logger


def cfg(**overrides):
    data = {"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
            "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}}
    data.update(overrides)
    return Config(data)


SRC_FIELDS = [
    {"name": "status", "default": True,
     "choices": {"2": ["Open", "Open"], "5": ["Closed", "Closed"], "9000": ["AI", "AI"]}},
    {"name": "priority", "default": True, "choices": {"Low": 1, "Urgent": 4}},
    {"name": "source", "default": True, "choices": {"Email": 1, "Whatsapp": 13}},
    {"name": "ticket_type", "default": True, "choices": ["Question", "Refund"]},
]


class TestFieldChoiceParsing(unittest.TestCase):
    def test_parses_all_three_shapes(self):
        c = _ticket_field_choices(FakeClient(get_map={"/ticket_fields": SRC_FIELDS}))
        self.assertEqual(c["status"], {2, 5, 9000})      # ids are the dict KEYS
        self.assertEqual(c["priority"], {1, 4})          # ids are the VALUES
        self.assertEqual(c["source"], {1, 13})
        self.assertEqual(c["type"], {"Question", "Refund"})


class TestPreflightCoverage(unittest.TestCase):
    def _run(self, tgt_fields, **cfgkw):
        src = FakeClient(get_map={"/ticket_fields": SRC_FIELDS,
                                  "/contact_fields": [], "/company_fields": []})
        tgt = FakeClient(get_map={"/ticket_fields": tgt_fields,
                                  "/contact_fields": [], "/company_fields": []})
        return preflight_coverage(cfg(**cfgkw), src, tgt, silent_logger())

    def test_clean_when_target_has_all_values(self):
        gaps = self._run(SRC_FIELDS)
        self.assertEqual(gaps, 0)

    def test_detects_missing_status_source_type(self):
        tgt = [
            {"name": "status", "default": True,
             "choices": {"2": ["Open", "Open"], "5": ["Closed", "Closed"]}},   # no 9000
            {"name": "priority", "default": True, "choices": {"Low": 1, "Urgent": 4}},
            {"name": "source", "default": True, "choices": {"Email": 1}},        # no 13
            {"name": "ticket_type", "default": True, "choices": ["Question"]},   # no Refund
        ]
        self.assertEqual(self._run(tgt), 3)

    def test_config_status_map_closes_the_gap(self):
        tgt = [
            {"name": "status", "default": True,
             "choices": {"2": ["Open", "Open"], "5": ["Closed", "Closed"]}},
            {"name": "priority", "default": True, "choices": {"Low": 1, "Urgent": 4}},
            {"name": "source", "default": True, "choices": {"Email": 1, "Whatsapp": 13}},
            {"name": "ticket_type", "default": True, "choices": ["Question", "Refund"]},
        ]
        # map source 9000 -> 5 (a valid target status): the only gap disappears.
        self.assertEqual(self._run(tgt, mapping={"status": {9000: 5}}), 0)


if __name__ == "__main__":
    unittest.main()
