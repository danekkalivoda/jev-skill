"""Offline checks that default routing (no settings file) keeps its tier decisions.

Run: python3 -m unittest discover tests
No network: decide() gets fixed Jev answers. HOME points at an empty folder so no shell profile,
settings file or Codex cache is read.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

EMPTY = tempfile.mkdtemp()
os.environ.update({"HOME": EMPTY, "XDG_CONFIG_HOME": EMPTY, "JEV_PROVIDER": "typesafe", "JEV_LOG": os.path.join(EMPTY, "log.jsonl")})
os.environ.pop("JEV_SETTINGS", None)
os.chdir(EMPTY)  # no .jev.json above the working directory

spec = importlib.util.spec_from_file_location("route", Path(__file__).resolve().parents[1] / "skills/jev/route.py")
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


def answers(probabilities, confidence=0.8, risky=0.1, unclear=0.1, tid="t"):
    return {
        f"tier:{tid}": {"score": 1.0, "confidence": confidence, "probabilities": {str(k): v for k, v in enumerate(probabilities)}},
        f"risky:{tid}": {"noul": risky},
        f"unclear:{tid}": {"noul": unclear},
    }


MECHANICAL = [0.85, 0.1, 0.05, 0.0]
HARD = [0.05, 0.2, 0.6, 0.15]  # covers at tier 2, with a real chance of tier 1
FRONTIER_LEANING_HARD = [0.0, 0.05, 0.4, 0.55]  # covers at tier 3, P(tier <= 2) 0.45
FRONTIER = [0.0, 0.05, 0.3, 0.65]  # covers at tier 3, P(tier <= 2) 0.35
STANDARD = [0.1, 0.75, 0.15, 0.0]


class DefaultRouting(unittest.TestCase):
    def tier(self, task=None, **kwargs):
        probabilities = kwargs.pop("probabilities")
        return route.decide(task or {"id": "t"}, answers(probabilities, **kwargs), None)

    def test_settings_are_default(self):
        self.assertEqual((route.QUALITY, route.TOPICS, route.SETTINGS_WARNINGS), (0, {}, []))

    def test_mechanical(self):
        self.assertEqual(self.tier(probabilities=MECHANICAL)["tier"], 0)

    def test_hard_drops_to_standard(self):
        self.assertEqual(self.tier(probabilities=HARD)["tier"], 1)

    def test_hard_kept_when_somewhat_risky(self):
        self.assertEqual(self.tier(probabilities=HARD, risky=0.5)["tier"], 2)

    def test_risky_floor(self):
        self.assertEqual(self.tier(probabilities=MECHANICAL, risky=0.8)["tier"], 2)

    def test_frontier_drops_to_hard(self):
        self.assertEqual(self.tier(probabilities=FRONTIER_LEANING_HARD)["tier"], 2)

    def test_frontier_kept(self):
        self.assertEqual(self.tier(probabilities=FRONTIER)["tier"], 3)

    def test_low_confidence_drops_one(self):
        self.assertEqual(self.tier(probabilities=STANDARD, confidence=0.2)["tier"], 0)

    def test_unclear_asks_first(self):
        self.assertTrue(self.tier(probabilities=STANDARD, unclear=0.95)["clarify_first"])

    def test_fallback(self):
        decision = route.decide({"id": "t"}, None, "timeout")
        self.assertEqual((decision["tier"], decision["skill_hint"]), (1, None))

    def test_orchestrator_floor(self):
        self.assertEqual(self.tier({"id": "t", "min_tier": 3}, probabilities=MECHANICAL)["tier"], 3)

    def test_retry_at_top_escalates(self):
        decision = self.tier({"id": "t", "attempt": 1}, probabilities=FRONTIER)
        self.assertEqual((decision["tier"], decision["escalate_to_user"]), (3, True))

    def test_models(self):
        decision = self.tier(probabilities=HARD, risky=0.5)
        self.assertEqual(decision["claude"], {"model": "opus"})
        self.assertEqual(decision["codex"], {"model": "gpt-6-sol", "reasoning_effort": "high"})


if __name__ == "__main__":
    unittest.main()
