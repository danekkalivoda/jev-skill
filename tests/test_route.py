"""Offline checks that default routing (no settings file) keeps its tier decisions.

Run: python3 -m unittest discover tests
No network: decide() gets fixed Jev answers. HOME points at an empty folder so no shell profile,
settings file or Codex cache is read.
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock
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


class RetryAndCap(unittest.TestCase):
    def decide(self, task, probabilities=STANDARD, **kwargs):
        return route.decide({"id": "t", **task}, answers(probabilities, **kwargs), None)

    def test_without_prev_tier_retry_is_unchanged(self):
        self.assertEqual(self.decide({"attempt": 1})["tier"], 2)  # fresh tier 1 + 1

    def test_prev_tier_steps_up_from_failed_tier(self):
        decision = self.decide({"attempt": 1, "prev_tier": 1}, probabilities=FRONTIER)
        self.assertEqual((decision["tier"], decision["escalate_to_user"]), (3, False))  # fresh tier 3 is already above
        decision = self.decide({"attempt": 1, "prev_tier": 1}, probabilities=STANDARD)
        self.assertEqual((decision["tier"], decision["escalate_to_user"]), (2, False))
        self.assertTrue(any("tier 1 failed" in line for line in decision["reasons"]))

    def test_prev_tier_does_not_double_count(self):
        # old behaviour would give 0 + 2 = 2 here; the failed tier was 0, so 1 is enough
        self.assertEqual(self.decide({"attempt": 2, "prev_tier": 0}, probabilities=MECHANICAL)["tier"], 1)

    def test_prev_tier_below_fresh_keeps_fresh(self):
        self.assertEqual(self.decide({"attempt": 1, "prev_tier": 0}, probabilities=FRONTIER)["tier"], 3)

    def test_escalates_only_when_prev_tier_is_top(self):
        self.assertFalse(self.decide({"attempt": 1, "prev_tier": 2}, probabilities=FRONTIER)["escalate_to_user"])
        self.assertTrue(self.decide({"attempt": 1, "prev_tier": 3}, probabilities=STANDARD)["escalate_to_user"])

    def test_prev_tier_ignored_on_first_attempt_and_when_invalid(self):
        self.assertEqual(self.decide({"prev_tier": 2})["tier"], 1)
        self.assertEqual(self.decide({"attempt": 1, "prev_tier": 7})["tier"], 2)

    def test_max_tier_caps_and_logs_reason(self):
        decision = self.decide({"max_tier": 1, "max_tier_reason": "user wants sonnet"}, probabilities=FRONTIER)
        self.assertEqual(decision["tier"], 1)
        self.assertTrue(any("user wants sonnet" in line for line in decision["reasons"]))

    def test_max_tier_wins_over_min_tier(self):
        decision = self.decide({"min_tier": 3, "max_tier": 1}, probabilities=MECHANICAL)
        self.assertEqual(decision["tier"], 1)
        self.assertTrue(any("overrides orchestrator floor" in line for line in decision["reasons"]))

    def test_max_tier_caps_retry(self):
        self.assertEqual(self.decide({"attempt": 1, "prev_tier": 1, "max_tier": 1})["tier"], 1)

    def test_max_tier_on_fallback(self):
        self.assertEqual(route.decide({"id": "t", "max_tier": 0}, None, "down")["tier"], 0)


class ProviderFallback(unittest.TestCase):
    """main() with the network mocked: local is probed, then typesafe takes over when local is down."""

    def setUp(self):
        self.addCleanup(route.use_provider, route.PROVIDER_NAME)
        route.use_provider("local")
        self.asked = []

    def run_main(self, up=True, local_fails=False, key="k"):
        settings = {"JEV_LOCAL_URL": "http://nimble.ts.net:8080/v1/systemone", "TYPESAFE_API_KEY": key}

        def ask(tasks):
            self.asked.append(route.PROVIDER_NAME)
            if route.PROVIDER_NAME == "local" and local_fails:
                raise RuntimeError("Jev request failed: boom")
            return {"answers": answers(STANDARD), "model": route.PROVIDER["model"]}

        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"tasks": [{"id": "t", "brief": "do it"}]}))
        with mock.patch.object(route, "load_setting", side_effect=settings.get), \
                mock.patch.object(route, "reachable", return_value=up) as probe, \
                mock.patch.object(route, "ask_jev", side_effect=ask), \
                mock.patch.object(route, "installed_skills", return_value={}), \
                mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out):
            self.assertEqual(route.main(), 0)
        self.probe = probe
        return json.loads(out.getvalue())

    def test_local_up_stays_local(self):
        output = self.run_main()
        self.assertEqual((output["provider"], output["source"], self.asked), ("local", "jev", ["local"]))
        self.probe.assert_called_once_with("http://nimble.ts.net:8080/v1/systemone", route.PROVIDER["connect_timeout_seconds"])

    def test_local_unreachable_uses_typesafe(self):
        output = self.run_main(up=False)
        self.assertEqual((output["provider"], output["source"], self.asked), ("typesafe", "jev", ["typesafe"]))
        self.assertTrue(any("local skipped" in line for line in output["decisions"][0]["reasons"]))

    def test_local_failing_retries_wave_on_typesafe(self):
        output = self.run_main(local_fails=True)
        self.assertEqual((output["provider"], output["source"], self.asked), ("typesafe", "jev", ["local", "typesafe"]))
        self.assertTrue(any("local skipped" in line for line in output["decisions"][0]["reasons"]))

    def test_no_key_falls_back_to_tier_one(self):
        output = self.run_main(up=False, key=None)
        self.assertEqual((output["provider"], output["source"], self.asked), ("local", "fallback", []))
        self.assertEqual(output["decisions"][0]["tier"], 1)

    def test_typesafe_policy_used_after_switch(self):
        self.run_main(up=False)
        self.assertEqual(route.POLICY["low_confidence_threshold"], route.CONFIG["policy"]["low_confidence_threshold"])

    def test_local_numbers_come_from_config(self):
        local = route.CONFIG["providers"]["local"]
        self.assertEqual(local["fallback_provider"], "typesafe")
        self.assertLessEqual(local["connect_timeout_seconds"], 2)
        self.assertLess(local["policy"]["timeout_seconds"], 15)


if __name__ == "__main__":
    unittest.main()
