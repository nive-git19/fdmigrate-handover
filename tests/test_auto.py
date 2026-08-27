"""runner.run_auto orchestration + gate logic. The heavy lifters (preflight_coverage,
run_migration, deep_verify, completeness_report, build_clients) are patched, so this
tests ONLY the gate/abort control flow - the part unique to auto mode."""
import unittest
from unittest import mock

from fdmigrate import runner
from fdmigrate.config import Config
from tests._fakes import FakeClient, silent_logger


def cfg():
    return Config({"source": {"domain": "https://a.freshdesk.com", "api_key": "x"},
                   "target": {"domain": "https://b.freshdesk.com", "api_key": "y"}})


CLEAN_VERIFY = {"clean": 25, "mismatch": 0, "failed": 0, "total": 25}
CLEAN_COMPLETE = {"ticket": {"source": 1, "done": 1, "partial": 0, "failed": 0, "missing": 0}}


class TestRunAutoGates(unittest.TestCase):
    def setUp(self):
        self.p = {
            "build_clients": mock.patch.object(runner, "build_clients",
                                               return_value=(FakeClient(), FakeClient())),
            "run_migration": mock.patch.object(runner, "run_migration", return_value=0),
            "deep_verify": mock.patch.object(runner, "deep_verify", return_value=CLEAN_VERIFY),
            "completeness_report": mock.patch.object(runner, "completeness_report",
                                                     return_value=CLEAN_COMPLETE),
            "preflight_coverage": mock.patch.object(runner, "preflight_coverage", return_value=0),
        }
        self.m = {k: v.start() for k, v in self.p.items()}
        self.addCleanup(lambda: [v.stop() for v in self.p.values()])

    def test_happy_path_returns_0_and_runs_full(self):
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger(), sample=25)
        self.assertEqual(rc, 0)
        # run_migration called for foundation, sample, and full (>=3)
        self.assertGreaterEqual(self.m["run_migration"].call_count, 3)

    def test_gate1_aborts_when_target_unreachable(self):
        bad = FakeClient()
        bad.whoami = mock.Mock(side_effect=Exception("401"))
        self.m["build_clients"].return_value = (FakeClient(), bad)
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger())
        self.assertEqual(rc, 1)
        self.m["run_migration"].assert_not_called()          # never started migrating

    def test_gate2_aborts_on_coverage_gap_unless_forced(self):
        self.m["preflight_coverage"].return_value = 3
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger(), force=False)
        self.assertEqual(rc, 1)
        self.m["run_migration"].assert_not_called()

    def test_gate2_force_proceeds_despite_gap(self):
        self.m["preflight_coverage"].return_value = 3
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger(), force=True)
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(self.m["run_migration"].call_count, 3)

    def test_gate3_aborts_before_full_run_on_sample_mismatch(self):
        self.m["deep_verify"].return_value = {"clean": 20, "mismatch": 5, "failed": 0, "total": 25}
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger())
        self.assertEqual(rc, 1)
        # foundation + sample ran (2), but NOT the full run (would be the 3rd+)
        self.assertEqual(self.m["run_migration"].call_count, 2)

    def test_gate4_attention_when_completeness_incomplete(self):
        self.m["completeness_report"].return_value = {
            "ticket": {"source": 10, "done": 8, "partial": 0, "failed": 1, "missing": 1}}
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger())
        self.assertEqual(rc, 3)          # finished, but flagged for attention

    def test_dry_run_skips_verification_gates(self):
        rc = runner.run_auto(cfg(), mock.MagicMock(), silent_logger(), dry_run=True)
        self.assertEqual(rc, 0)
        self.m["deep_verify"].assert_not_called()
        self.m["completeness_report"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
