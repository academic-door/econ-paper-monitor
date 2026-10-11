from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "infra/cloudflare/daily-watchdog-trigger/worker.mjs"
WRANGLER = ROOT / "infra/cloudflare/daily-watchdog-trigger/wrangler.toml"
WATCHDOG = ROOT / ".github/workflows/watchdog.yml"
PRESENCE = ROOT / ".github/workflows/deploy-presence.yml"
DEPLOY = ROOT / ".github/workflows/deploy-daily-watchdog-trigger.yml"


class DailyWatchdogExternalTriggerContract(unittest.TestCase):
    def test_only_fixed_watchdog_dispatch_and_no_http_ingress(self):
        text = WORKER.read_text(encoding="utf-8")
        self.assertIn("academic-door/econ-paper-monitor/actions/workflows/watchdog.yml/dispatches", text)
        self.assertIn("ref: 'main'", text)
        self.assertIn("GITHUB_ACTIONS_DISPATCH_TOKEN", text)
        self.assertNotIn("async fetch(", text)
        for forbidden in ("fast-discovery.yml/dispatches", "update.yml/dispatches", "nber-working-papers", "publish-site"):
            self.assertNotIn(forbidden, text)

    def test_separate_worker_and_quarter_hour_cron(self):
        config = WRANGLER.read_text(encoding="utf-8")
        self.assertIn('name = "academic-door-daily-watchdog-trigger"', config)
        self.assertIn('crons = ["4,19,34,49 * * * *"]', config)
        self.assertIn('workers_dev = false', config)

    def test_watchdog_attribution_native_redundancy_and_serialization(self):
        text = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn('cron: "11,26,41,56 * * * *"', text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("WATCHDOG_RUN_ID=", text)
        self.assertIn("WATCHDOG_TRIGGER_SOURCE=", text)
        self.assertIn("cloudflare_cron_claimed", text)
        self.assertIn("gh workflow run fast-discovery.yml", text)
        self.assertIn("gh workflow run update.yml", text)

    def test_isolated_cloudflare_deployment(self):
        deploy = DEPLOY.read_text(encoding="utf-8")
        presence = PRESENCE.read_text(encoding="utf-8")
        self.assertIn("workingDirectory: infra/cloudflare/daily-watchdog-trigger", deploy)
        self.assertIn("CLOUDFLARE_API_TOKEN", deploy)
        self.assertNotIn('      - "infra/**"', presence)
        self.assertIn('      - "infra/presence-worker.js"', presence)

    def test_node_worker_behavior(self):
        result = subprocess.run(
            ["node", "--test", "tests/test_daily_watchdog_external_trigger.mjs"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()
