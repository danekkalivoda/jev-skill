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


    def test_frontier_claude_fallback(self):
        decision = self.tier(probabilities=FRONTIER)
        self.assertEqual(decision["tier"], 3)
        self.assertEqual(decision["claude"]["fallback"], ["opus"])


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


def load_settings(user=None, project=None):
    """Run the settings loader on temp files. Returns (quality, topics, agents, warnings)."""
    folder = Path(tempfile.mkdtemp())
    env = {"JEV_SETTINGS": str(folder / "user.json")}
    if user is not None:
        (folder / "user.json").write_text(json.dumps(user))
    work = folder / "project/sub"
    work.mkdir(parents=True)
    if project is not None:
        (folder / "project/.jev.json").write_text(json.dumps(project))
    before = os.getcwd()
    os.chdir(work)
    try:
        with mock.patch.dict(os.environ, env):
            return route.load_all_settings()
    finally:
        os.chdir(before)


DAN = {
    "topics": {"security": 2, "visual": {"weight": 2, "description": "visual design"}},
    "agents": {
        "tiers": {"0": "codex", "1": "codex", "2": "claude", "3": "claude"},
        "claude_only": {"topics": ["security", "visual"]},
    },
}


class AgentSettings(unittest.TestCase):
    def test_no_section_is_unconfigured(self):
        _, _, agents, warnings = load_settings({"quality": 1})
        self.assertFalse(agents["configured"])
        self.assertEqual(warnings, [])
        self.assertFalse(route.AGENTS["configured"])

    def test_dan_config_parses_cleanly(self):
        _, _, agents, warnings = load_settings(DAN)
        self.assertEqual(warnings, [])
        self.assertTrue(agents["configured"])
        self.assertEqual({tier: spec["agent"] for tier, spec in agents["tiers"].items()}, {0: "codex", 1: "codex", 2: "claude", 3: "claude"})
        self.assertEqual(agents["claude_only_topics"], ["security", "visual"])
        self.assertEqual(agents["claude_only_kinds"], ["review", "check", "critic", "advisor"])
        self.assertTrue(agents["fallback"])

    def test_project_wins_per_tier_and_replaces_lists(self):
        project = {"agents": {"tiers": {"1": {"agent": "claude"}, "2": {"agent": "codex", "model": "astra", "effort": "high"}},
                              "claude_only": {"kinds": ["review"]}, "fallback": False}}
        _, _, agents, warnings = load_settings(DAN, project)
        self.assertEqual(warnings, [])
        self.assertEqual(agents["tiers"][0]["agent"], "codex")  # untouched user value
        self.assertEqual(agents["tiers"][1]["agent"], "claude")
        self.assertEqual(agents["tiers"][2], {"agent": "codex", "model": "astra", "effort": "high"})
        self.assertEqual(agents["claude_only_kinds"], ["review"])
        self.assertEqual(agents["claude_only_topics"], ["security", "visual"])
        self.assertFalse(agents["fallback"])

    def test_default_agent_and_read_only_tiers(self):
        _, _, agents, warnings = load_settings({"agents": {"default": "codex", "read_only": {"tiers": {"2": "claude"}}}})
        self.assertEqual(warnings, [])
        self.assertEqual(agents["default"], "codex")
        self.assertEqual(agents["read_only_tiers"][2]["agent"], "claude")

    def test_invalid_values_warn_and_are_skipped(self):
        bad = {"agents": {
            "default": "gemini",
            "tiers": {"0": "gemini", "7": "codex", "x": "codex", "1": {"agent": "codex", "model": "opus"},
                      "2": {"agent": "claude", "model": "sol"}, "3": {"agent": "codex", "effort": "turbo"}},
            "claude_only": {"topics": ["nonsense"], "kinds": "review"},
            "fallback": "yes", "bogus": 1}}
        _, _, agents, warnings = load_settings(bad)
        text = "\n".join(warnings)
        for needle in ("default", "tier 0", "tier key 7", "tier key x", "tier 1", "tier 2", "tier 3", "nonsense", "kinds", "fallback", "bogus"):
            self.assertIn(needle, text)
        self.assertEqual(agents["tiers"], {})
        self.assertEqual(agents["default"], "claude")

    def test_agents_not_an_object(self):
        _, _, agents, warnings = load_settings({"agents": ["codex"]})
        self.assertFalse(agents["configured"])
        self.assertTrue(any("agents must be an object" in line for line in warnings))


def with_agents(settings, topics=None):
    """Context manager: route uses the agent settings parsed from `settings`."""
    _, parsed_topics, agents, _ = load_settings(settings)
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(route, "AGENTS", agents))
    stack.enter_context(mock.patch.object(route, "TOPICS", topics if topics is not None else parsed_topics))
    return stack


class AgentChoice(unittest.TestCase):
    def decide(self, settings, task=None, probabilities=STANDARD, topic=None, **kwargs):
        data = answers(probabilities, **kwargs)
        data["topic:security:t"] = {"noul": 0.1 if topic is None else topic}
        data["topic:visual:t"] = {"noul": 0.1}
        with with_agents(settings, {"security": (1, "security work"), "visual": (1, "visual work")}):
            return route.decide({"id": "t", **(task or {})}, data, None)

    def test_default_output_unchanged_except_new_fields(self):
        decision = route.decide({"id": "t"}, answers(STANDARD), None)
        self.assertEqual((decision["agent"], decision["model"], decision["effort"], decision["alternate"]), ("claude", "sonnet", None, None))
        self.assertFalse(any(line.startswith("agent ") for line in decision["reasons"]))
        self.assertEqual(decision["claude"], {"model": "sonnet"})

    def test_configured_agents_never_change_tier(self):
        for probabilities in (MECHANICAL, STANDARD, HARD, FRONTIER_LEANING_HARD, FRONTIER):
            for extra in ({}, {"min_tier": 2}, {"attempt": 1, "prev_tier": 1}, {"max_tier": 1}):
                plain = route.decide({"id": "t", **extra}, answers(probabilities), None)
                routed = self.decide(DAN, extra, probabilities)
                self.assertEqual(routed["tier"], plain["tier"])
                self.assertEqual(routed["claude"], plain["claude"])
                self.assertEqual(routed["codex"], plain["codex"])

    def test_tier_one_goes_to_codex_with_claude_alternate(self):
        decision = self.decide(DAN)
        self.assertEqual(decision["tier"], 1)
        self.assertEqual((decision["agent"], decision["model"], decision["effort"]), ("codex", "gpt-6-sol", "medium"))
        self.assertEqual(decision["alternate"], {"agent": "claude", "model": "sonnet", "effort": None})
        self.assertTrue(any(line.startswith("agent codex") for line in decision["reasons"]))

    def test_tier_two_stays_claude_with_codex_alternate(self):
        decision = self.decide(DAN, probabilities=HARD, risky=0.5)
        self.assertEqual(decision["tier"], 2)
        self.assertEqual((decision["agent"], decision["model"], decision["effort"]), ("claude", "opus", None))
        self.assertEqual(decision["alternate"], {"agent": "codex", "model": "gpt-6-sol", "effort": "high"})

    def test_model_and_effort_override(self):
        settings = {"agents": {"tiers": {"2": {"agent": "codex", "model": "astra", "effort": "xhigh"}, "1": {"agent": "codex", "model": "gpt-6.2-sol"}}}}
        decision = self.decide(settings, probabilities=HARD, risky=0.5)
        self.assertEqual((decision["agent"], decision["model"], decision["effort"]), ("codex", "gpt-6-astra", "xhigh"))
        decision = self.decide(settings)
        self.assertEqual((decision["model"], decision["effort"]), ("gpt-6.2-sol", "medium"))

    def test_claude_model_override(self):
        decision = self.decide({"agents": {"tiers": {"1": {"agent": "claude", "model": "opus", "effort": "high"}}}})
        self.assertEqual((decision["agent"], decision["model"], decision["effort"]), ("claude", "opus", "high"))
        self.assertEqual(decision["alternate"]["agent"], "codex")

    def test_reviews_stay_on_claude(self):
        for kind in ("review", "Check", "critic", "advisor"):
            decision = self.decide(DAN, {"kind": kind})
            self.assertEqual((decision["agent"], decision["model"], decision["alternate"]), ("claude", "sonnet", None), kind)
            self.assertTrue(any("kind" in line for line in decision["reasons"] if line.startswith("agent ")))

    def test_reviews_exception_can_be_turned_off(self):
        settings = {"agents": {"tiers": {"1": "codex"}, "claude_only": {"kinds": []}}}
        self.assertEqual(self.decide(settings, {"kind": "review"})["agent"], "codex")

    def test_topic_match_stays_on_claude(self):
        decision = self.decide(DAN, topic=0.9)
        self.assertEqual((decision["agent"], decision["alternate"]), ("claude", None))
        self.assertTrue(any("security" in line for line in decision["reasons"] if line.startswith("agent ")))
        self.assertEqual(self.decide(DAN, topic=0.2)["agent"], "codex")

    def test_unknown_topics_stay_on_claude(self):
        with with_agents(DAN, {"security": (2, "security work")}):
            decision = route.decide({"id": "t"}, None, "jev down")
        self.assertEqual((decision["tier"], decision["agent"]), (1, "claude"))

    def test_unknown_topics_do_not_matter_without_topic_exceptions(self):
        settings = {"agents": {"tiers": {"1": "codex"}}}
        with with_agents(settings, {}):
            self.assertEqual(route.decide({"id": "t"}, None, "jev down")["agent"], "codex")

    def test_read_only_tasks_use_their_own_tiers(self):
        settings = {"agents": {"tiers": {"1": "claude"}, "read_only": {"tiers": {"1": "codex"}}}}
        self.assertEqual(self.decide(settings)["agent"], "claude")
        self.assertEqual(self.decide(settings, {"read_only": True})["agent"], "codex")

    def test_task_agent_wins(self):
        decision = self.decide(DAN, {"agent": "claude", "agent_reason": "user asked"})
        self.assertEqual((decision["agent"], decision["alternate"]), ("claude", None))
        self.assertTrue(any("user asked" in line for line in decision["reasons"]))
        decision = self.decide(DAN, {"agent": "codex", "kind": "review"})
        self.assertEqual(decision["agent"], "codex")
        self.assertEqual(self.decide(DAN, {"agent": "gemini"})["agent"], "codex")  # invalid value is ignored

    def test_claude_only_tasks_never_get_alternate(self):
        for tier_probabilities in (STANDARD, FRONTIER):
            decision = self.decide(DAN, {"kind": "review"}, probabilities=tier_probabilities)
            self.assertEqual((decision["agent"], decision["alternate"]), ("claude", None))
        decision = self.decide(DAN, probabilities=HARD, risky=0.5, topic=0.7)
        self.assertEqual((decision["tier"], decision["agent"], decision["alternate"]), (2, "claude", None))

    def test_missing_topic_score_stays_on_claude(self):
        settings = {"agents": {"tiers": {"1": "codex"}, "claude_only": {"topics": ["security", "visual"]}}}
        data = answers(STANDARD)
        data["topic:security:t"] = {"noul": 0.1}  # visual has no answer
        with with_agents(settings, {"security": (1, "s"), "visual": (1, "v")}):
            decision = route.decide({"id": "t"}, data, None)
        self.assertEqual((decision["agent"], decision["alternate"]), ("claude", None))
        self.assertTrue(any("visual unknown" in line for line in decision["reasons"]))

    def test_kind_is_normalized(self):
        for kind in ("Review ", "code-review", "PR_check", "final critic pass"):
            self.assertEqual(self.decide(DAN, {"kind": kind})["agent"], "claude", kind)
        self.assertEqual(self.decide(DAN, {"kind": "implementation"})["agent"], "codex")
        self.assertEqual(self.decide(DAN, {"kind": "preview"})["agent"], "codex")

    def test_task_agent_warnings(self):
        decision = self.decide(DAN, {"agent": "gpt"})
        self.assertEqual(decision["agent"], "codex")
        self.assertTrue(any(line.startswith("warning:") and "gpt" in line for line in decision["reasons"]))
        decision = self.decide(DAN, {"agent": "codex", "kind": "review"})
        self.assertEqual((decision["agent"], decision["alternate"]), ("codex", None))
        self.assertTrue(any(line.startswith("warning:") and "overriding claude_only" in line for line in decision["reasons"]))
        self.assertFalse(any(line.startswith("warning:") for line in self.decide(DAN, {"agent": "codex"})["reasons"]))

    def test_full_claude_model_name_is_refused(self):
        _, _, agents, warnings = load_settings({"agents": {"tiers": {"1": {"agent": "claude", "model": "claude-opus-5-5"}}}})
        self.assertEqual(agents["tiers"], {})
        self.assertTrue(any("claude-" in line for line in warnings))

    def test_fallback_off_gives_no_alternate(self):
        settings = {"agents": {"tiers": {"1": "codex"}, "fallback": False}}
        self.assertIsNone(self.decide(settings)["alternate"])

    def test_default_codex_for_every_tier(self):
        decision = self.decide({"agents": {"default": "codex"}}, probabilities=FRONTIER)
        self.assertEqual((decision["tier"], decision["agent"], decision["model"], decision["effort"]), (3, "codex", "gpt-6-astra", "xhigh"))


class AgentOutputAndLog(unittest.TestCase):
    def run_main(self, settings):
        _, _, agents, warnings = load_settings(settings)
        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"tasks": [{"id": "t", "brief": "do it", "kind": "review"}, {"id": "u", "brief": "do that"}]}))
        both = {**answers(STANDARD), **answers(STANDARD, tid="u")}
        for tid in "tu":
            both.update({f"topic:security:{tid}": {"noul": 0.1}, f"topic:visual:{tid}": {"noul": 0.1}})
        log = Path(tempfile.mkdtemp()) / "log.jsonl"
        with mock.patch.object(route, "AGENTS", agents), mock.patch.object(route, "SETTINGS_WARNINGS", warnings), \
                mock.patch.object(route, "TOPICS", {"security": (1, "s"), "visual": (1, "v")}), \
                mock.patch.object(route, "LOG", log), \
                mock.patch.object(route, "endpoint", return_value=("http://x", {}, "m")), \
                mock.patch.object(route, "ask_jev", return_value={"answers": both, "model": "m"}), \
                mock.patch.object(route, "installed_skills", return_value={}), \
                mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out):
            self.assertEqual(route.main(), 0)
        return json.loads(out.getvalue()), json.loads(log.read_text().splitlines()[-1])

    def test_output_and_log(self):
        output, logged = self.run_main(DAN)
        review, work = output["decisions"]
        self.assertEqual((review["agent"], work["agent"]), ("claude", "codex"))
        self.assertEqual(output["settings"]["agents"]["tiers"]["1"], {"agent": "codex", "model": None, "effort": None})
        self.assertEqual([d["agent"] for d in logged["decisions"]], ["claude", "codex"])
        self.assertTrue(any(line.startswith("agent codex") for line in logged["decisions"][1]["reasons"]))
        self.assertEqual(logged["tasks"][0]["kind"], "review")

    def test_task_warnings_reach_settings(self):
        output, _ = self.run_main(DAN)  # task t is a review, no warning
        self.assertEqual(output["settings"]["warnings"], [])

    def test_unconfigured_output(self):
        output, _ = self.run_main({})
        self.assertIsNone(output["settings"]["agents"])
        self.assertEqual({d["agent"] for d in output["decisions"]}, {"claude"})


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
