import json
import os
import re
import unittest

from _load import ROOT

# the India pack: eleven folders, the US pack's ten ported plus the insider tracker
SKILLS = ["india-scanner", "india-news", "india-entry", "india-exit", "india-timemachine",
          "india-risk", "india-budget", "india-broker", "india-alerts", "india-analyst",
          "india-insider"]
ABOUT = ("11 Claude skills for intraday trading on NSE: scan thousands of charts, track NSE filings and "
         "news, time every entry and exit, and replay every outcome before you risk a rupee.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["india-scanner", "india-entry", "india-exit", "india-timemachine", "india-budget",
              "india-insider", "india-risk", "india-broker", "india-alerts"]
US_ONLY = ["trader-pelosi", "sec_user_agent", "place_stock_order", "pdt_guard", "_usd\""]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


def config():
    with open(os.path.join(ROOT, "india-budget", "config.example.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_holds_only_the_pack(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git"))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "docs", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "india-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "india-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "india-trader-skill")

    def test_readme_names_every_skill(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The India Trader skill"))
        for line in (ABOUT, "## Fine print", "not financial advice"):
            self.assertIn(line.lower(), readme.lower())
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.assertNotIn("\u2014", fh.read(), rel)

    def test_no_us_only_leftovers(self):
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith((".py", ".md", ".json")):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    text = fh.read()
                for old in US_ONLY:
                    self.assertNotIn(old, text, "%s/%s" % (s, f))

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_every_tool_reconfigures_stdout_for_the_rupee_sign(self):
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if f.endswith(".py"):
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        self.assertIn("reconfigure(", fh.read(), "%s/%s" % (s, f))

    def test_day_trading_limits_ship_in_the_example_config(self):
        cfg = config()
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by", "product"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertEqual(cfg["product"], "MIS")
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)
        # flat before the 15:12 Zerodha square-off of F&O stocks and the 15:15 closing auction
        self.assertLess(cfg["flat_by"], "15:12")

    def test_paper_trading_is_the_default(self):
        cfg = config()
        self.assertIs(cfg["paper"], True)
        self.assertEqual(cfg["broker"], "zerodha")


if __name__ == "__main__":
    unittest.main()
