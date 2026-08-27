"""tickets._build_payload - value mapping, marker tag, due-date guard,
requester cascade, responder/group/company mapping, custom-field strictness."""
import unittest

from fdmigrate.config import Config
from fdmigrate.phases import tickets
from tests._fakes import FakeCtx


def cfg(**overrides):
    data = {"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
            "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}}
    data.update(overrides)
    return Config(data)


def base_ticket(**over):
    t = {"id": 500, "subject": "Hi", "description": "<p>Body</p>",
         "status": 2, "priority": 1, "source": 1, "type": "Question",
         "tags": ["vip"], "requester_id": 10}
    t.update(over)
    return t


class TestBuildPayload(unittest.TestCase):
    def test_core_fields_and_marker_tag(self):
        ctx = FakeCtx(cfg())
        p = tickets._build_payload(ctx, base_ticket(), {"10": "111"}, {}, {}, {})
        self.assertEqual(p["subject"], "Hi")
        self.assertEqual(p["status"], 2)
        self.assertEqual(p["priority"], 1)
        self.assertIn("fd-migration-500", p["tags"])
        self.assertIn("vip", p["tags"])
        self.assertEqual(p["requester_id"], 111)   # mapped contact id, as int

    def test_value_mapping_overrides(self):
        c = cfg(mapping={"status": {2: 4}, "priority": {1: 3}, "source": {1: 2}})
        p = tickets._build_payload(FakeCtx(c), base_ticket(), {"10": "1"}, {}, {}, {})
        self.assertEqual(p["status"], 4)
        self.assertEqual(p["priority"], 3)
        self.assertEqual(p["source"], 2)

    def test_requester_email_fallback_when_unmapped(self):
        t = base_ticket(requester_id=99, requester={"email": "who@x.com"})
        p = tickets._build_payload(FakeCtx(cfg()), t, {}, {}, {}, {})  # empty contact_map
        self.assertNotIn("requester_id", p)
        self.assertEqual(p["email"], "who@x.com")

    def test_no_requester_logs_warning(self):
        ctx = FakeCtx(cfg())
        t = base_ticket(requester_id=None)
        t.pop("requester_id")
        p = tickets._build_payload(ctx, t, {}, {}, {}, {})
        self.assertNotIn("requester_id", p)
        self.assertNotIn("email", p)
        self.assertIn("no_requester", ctx.actions())

    def test_responder_group_company_mapped_via_idmaps(self):
        t = base_ticket(responder_id=7, group_id=8, company_id=9)
        p = tickets._build_payload(FakeCtx(cfg()), t, {"10": "1"},
                                   {"7": "70"}, {"8": "80"}, {"9": "90"})
        self.assertEqual(p["responder_id"], 70)
        self.assertEqual(p["group_id"], 80)
        self.assertEqual(p["company_id"], 90)

    def test_unmapped_responder_group_company_dropped(self):
        t = base_ticket(responder_id=7, group_id=8, company_id=9)
        p = tickets._build_payload(FakeCtx(cfg()), t, {"10": "1"}, {}, {}, {})
        self.assertNotIn("responder_id", p)
        self.assertNotIn("group_id", p)
        self.assertNotIn("company_id", p)

    def test_due_dates_kept_only_for_open_pending_and_future(self):
        # pending + future fr_due_by -> kept
        t = base_ticket(status=3, due_by="2999-01-01T00:00:00Z", fr_due_by="2999-01-01T00:00:00Z")
        p = tickets._build_payload(FakeCtx(cfg()), t, {"10": "1"}, {}, {}, {})
        self.assertIn("due_by", p)
        # resolved (status 4) -> dropped even if future
        t2 = base_ticket(status=4, due_by="2999-01-01T00:00:00Z", fr_due_by="2999-01-01T00:00:00Z")
        p2 = tickets._build_payload(FakeCtx(cfg()), t2, {"10": "1"}, {}, {}, {})
        self.assertNotIn("due_by", p2)
        # open but PAST fr_due_by -> dropped
        t3 = base_ticket(status=2, due_by="2999-01-01T00:00:00Z", fr_due_by="2000-01-01T00:00:00Z")
        p3 = tickets._build_payload(FakeCtx(cfg()), t3, {"10": "1"}, {}, {}, {})
        self.assertNotIn("due_by", p3)

    def test_custom_fields_strict_drops_unmapped(self):
        c = cfg()
        c.map_custom_fields = {"known": "tgt_known"}
        c.custom_field_strict = True
        t = base_ticket(custom_fields={"known": "v", "unknown": "z"})
        p = tickets._build_payload(FakeCtx(c), t, {"10": "1"}, {}, {}, {})
        self.assertEqual(p["custom_fields"], {"tgt_known": "v"})

    def test_created_date_custom_field_stamped(self):
        c = cfg()
        c.created_date_custom_field = "cf_orig"
        t = base_ticket(created_at="2021-01-01T00:00:00Z")
        p = tickets._build_payload(FakeCtx(c), t, {"10": "1"}, {}, {}, {})
        self.assertEqual(p["custom_fields"]["cf_orig"], "2021-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
