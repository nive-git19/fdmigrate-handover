"""runner.run_rollback - the marker-scoped undo. Preview vs --yes gating, delete
loop, store cleanup, failure handling. Clients + marker scan are patched (no network)."""
import unittest
from unittest import mock

from fdmigrate import runner
from fdmigrate.config import Config
from fdmigrate.phases import tickets as tickets_mod
from tests._fakes import FakeClient, FakeResp, silent_logger


def cfg():
    return Config({"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
                   "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}})


class TestRunRollback(unittest.TestCase):
    def setUp(self):
        self.tgt = FakeClient()
        self.bc = mock.patch.object(runner, "build_clients",
                                    return_value=(FakeClient(), self.tgt)).start()
        self.markers = {"1": "101", "2": "102", "3": "103"}
        self.pf = mock.patch.object(tickets_mod, "_prefetch_target_markers",
                                    return_value=self.markers).start()
        self.addCleanup(mock.patch.stopall)

    def test_no_markers_is_noop(self):
        self.pf.return_value = {}
        store = mock.MagicMock()
        rc = runner.run_rollback(cfg(), store, silent_logger(), confirm=True)
        self.assertEqual(rc, 0)
        store.remove_record.assert_not_called()

    def test_preview_without_confirm_deletes_nothing(self):
        store = mock.MagicMock()
        rc = runner.run_rollback(cfg(), store, silent_logger(), confirm=False)
        self.assertEqual(rc, 0)
        self.assertEqual([w for w in self.tgt.writes if w[0] == "DELETE"], [])  # no deletes
        store.remove_record.assert_not_called()

    def test_dry_run_deletes_nothing(self):
        store = mock.MagicMock()
        rc = runner.run_rollback(cfg(), store, silent_logger(), confirm=True, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual([w for w in self.tgt.writes if w[0] == "DELETE"], [])

    def test_confirm_deletes_all_and_cleans_store(self):
        store = mock.MagicMock()
        rc = runner.run_rollback(cfg(), store, silent_logger(), confirm=True)
        self.assertEqual(rc, 0)
        deletes = [w for w in self.tgt.writes if w[0] == "DELETE"]
        self.assertEqual(len(deletes), 3)                       # one per marker
        self.assertEqual(store.remove_record.call_count, 3)     # local state cleared

    def test_partial_failure_returns_2_and_only_cleans_deleted(self):
        # first delete fails (500), the rest succeed (204)
        seq = [FakeResp(500), FakeResp(204), FakeResp(204)]
        self.tgt.delete = lambda path, **kw: seq.pop(0)
        store = mock.MagicMock()
        rc = runner.run_rollback(cfg(), store, silent_logger(), confirm=True)
        self.assertEqual(rc, 2)
        self.assertEqual(store.remove_record.call_count, 2)     # only the 2 that deleted


if __name__ == "__main__":
    unittest.main()
