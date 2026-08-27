"""D2 archived_feature_state classification + client dry-run write suppression."""
import unittest

from fdmigrate.client import FreshdeskClient, _DryResponse
from fdmigrate.phases import tickets
from tests._fakes import FakeClient, FakeResp, silent_logger


class TestArchivedFeatureState(unittest.TestCase):
    def test_unavailable_on_require_feature_403(self):
        c = FakeClient(raw_map={"/tickets/archived/1":
                                FakeResp(403, text='{"code":"require_feature"}')})
        self.assertEqual(tickets.archived_feature_state(c), "unavailable")

    def test_available_on_200_or_404(self):
        self.assertEqual(tickets.archived_feature_state(
            FakeClient(raw_map={"/tickets/archived/1": FakeResp(404)})), "available")
        self.assertEqual(tickets.archived_feature_state(
            FakeClient(raw_map={"/tickets/archived/1": FakeResp(200, {"id": 1})})), "available")

    def test_unknown_on_other_status(self):
        c = FakeClient(raw_map={"/tickets/archived/1": FakeResp(500)})
        self.assertEqual(tickets.archived_feature_state(c), "unknown")


class TestClientDryRun(unittest.TestCase):
    def _client(self):
        return FreshdeskClient("https://b.freshdesk.com", "key", silent_logger(),
                               label="target", dry_run=True)

    def test_writes_return_dry_response_without_network(self):
        c = self._client()
        r_post = c.post("/tickets", json={"subject": "x"})
        r_put = c.put("/tickets/1", json={})
        r_del = c.delete("/tickets/1")
        self.assertIsInstance(r_post, _DryResponse)
        self.assertEqual(r_post.status_code, 201)
        self.assertEqual(r_put.status_code, 200)
        self.assertEqual(r_del.status_code, 200)

    def test_dry_ids_are_unique_and_negative(self):
        c = self._client()
        id1 = c.post("/tickets", json={}).json()["id"]
        id2 = c.post("/tickets", json={}).json()["id"]
        self.assertNotEqual(id1, id2)
        self.assertLess(id1, 0)
        self.assertLess(id2, 0)


if __name__ == "__main__":
    unittest.main()
