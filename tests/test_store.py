"""store.Store - dedup/resume backbone: upsert/get_record, target_map (done only),
clear_failed, reset_entity, conversation dedup. Uses a throwaway temp DB."""
import os
import tempfile
import unittest

from fdmigrate.store import Store


class TestStore(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            try:
                os.remove(p)
            except OSError:
                pass

    def test_upsert_and_get_record(self):
        self.store.upsert("ticket", 1, 100, name="A", status="done")
        rec = self.store.get_record("ticket", 1)
        self.assertEqual(rec["target_id"], "100")
        self.assertEqual(rec["status"], "done")

    def test_target_map_only_returns_done(self):
        self.store.upsert("ticket", 1, 100, status="done")
        self.store.upsert("ticket", 2, None, status="failed")
        self.store.upsert("ticket", 3, 300, status="partial")
        self.assertEqual(self.store.target_map("ticket"), {"1": "100"})

    def test_clear_failed_removes_failed_and_partial_only(self):
        self.store.upsert("ticket", 1, 100, status="done")
        self.store.upsert("ticket", 2, None, status="failed")
        self.store.upsert("ticket", 3, 300, status="partial")
        cleared = self.store.clear_failed("ticket")
        self.assertEqual(cleared, 2)
        self.assertIsNotNone(self.store.get_record("ticket", 1))     # done survives
        self.assertIsNone(self.store.get_record("ticket", 2))
        self.assertIsNone(self.store.get_record("ticket", 3))

    def test_clear_failed_scoped_by_entity(self):
        self.store.upsert("ticket", 1, None, status="failed")
        self.store.upsert("contact", 2, None, status="failed")
        self.store.clear_failed("ticket")
        self.assertIsNone(self.store.get_record("ticket", 1))
        self.assertIsNotNone(self.store.get_record("contact", 2))    # untouched

    def test_reset_entity_forgets_all_and_clears_conversations(self):
        self.store.upsert("ticket", 1, 100, status="done")
        self.store.mark_conversation(1, 55, "done")
        self.store.upsert("contact", 9, 90, status="done")
        removed = self.store.reset_entity("ticket")
        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.get_record("ticket", 1))
        self.assertFalse(self.store.conversation_done(1, 55))        # conversations wiped
        self.assertIsNotNone(self.store.get_record("contact", 9))    # other entity kept

    def test_conversation_dedup(self):
        self.assertFalse(self.store.conversation_done(1, 55))
        self.store.mark_conversation(1, 55, "done")
        self.assertTrue(self.store.conversation_done(1, 55))


if __name__ == "__main__":
    unittest.main()
