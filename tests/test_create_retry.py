"""tickets._create_ticket - the shared create path with company_id + due_by
graceful-degrade retries (used by both the live loop and the archived path)."""
import unittest

from fdmigrate.config import Config
from fdmigrate.phases import tickets
from tests._fakes import FakeClient, FakeCtx, FakeResp


def cfg():
    return Config({"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
                   "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}})


class TestCreateTicketRetries(unittest.TestCase):
    def test_success_first_try(self):
        tgt = FakeClient(post_seq=[FakeResp(201, {"id": 42})])
        ctx = FakeCtx(cfg(), tgt=tgt)
        resp = tickets._create_ticket(ctx, 1, {"subject": "x"}, [])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(tgt.writes), 1)

    def test_company_id_rejected_then_retried_without_it(self):
        tgt = FakeClient(post_seq=[
            FakeResp(400, text='{"errors":[{"field":"company_id"}]}'),
            FakeResp(201, {"id": 42})])
        ctx = FakeCtx(cfg(), tgt=tgt)
        payload = {"subject": "x", "company_id": 9}
        resp = tickets._create_ticket(ctx, 1, payload, [])
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn("company_id", payload)          # popped before retry
        self.assertIn("company_dropped", ctx.actions())
        self.assertEqual(len(tgt.writes), 2)

    def test_due_by_rejected_then_retried_without_dates(self):
        tgt = FakeClient(post_seq=[
            FakeResp(400, text='{"errors":[{"field":"fr_due_by","message":'
                               '"cannot be set, when the status ... sla timer on"}]}'),
            FakeResp(201, {"id": 42})])
        ctx = FakeCtx(cfg(), tgt=tgt)
        payload = {"subject": "x", "due_by": "2999-01-01T00:00:00Z",
                   "fr_due_by": "2999-01-01T00:00:00Z"}
        resp = tickets._create_ticket(ctx, 1, payload, [])
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn("due_by", payload)
        self.assertNotIn("fr_due_by", payload)
        self.assertIn("due_dates_dropped", ctx.actions())

    def test_unrelated_400_not_retried(self):
        tgt = FakeClient(post_seq=[FakeResp(400, text='{"errors":[{"field":"subject"}]}')])
        ctx = FakeCtx(cfg(), tgt=tgt)
        resp = tickets._create_ticket(ctx, 1, {"subject": ""}, [])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(tgt.writes), 1)             # no retry


if __name__ == "__main__":
    unittest.main()
