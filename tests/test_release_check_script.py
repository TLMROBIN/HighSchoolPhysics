import unittest
from pathlib import Path


class ReleaseCheckScriptTests(unittest.TestCase):
    def test_hsp_release_check_captures_release_gate_commands(self):
        script = Path("scripts/hsp_release_check.sh").read_text(encoding="utf-8")

        self.assertIn("compileall -q highschoolphysics tools tests", script)
        self.assertIn("node --check highschoolphysics/assets/app.js", script)
        self.assertIn("unittest discover -s tests -v", script)
        self.assertIn("highschoolphysics.runtime_check --json", script)
        self.assertIn("git diff --check", script)

    def test_hsp_release_check_has_completion_gate_switches(self):
        script = Path("scripts/hsp_release_check.sh").read_text(encoding="utf-8")

        self.assertIn("VERIFY_TARGET", script)
        self.assertIn("REMOTE_HOST", script)
        self.assertIn("REMOTE_DIR", script)
        self.assertIn("REMOTE_GITHUB_URL", script)
        self.assertIn("REMOTE_PUBLIC_BASE_URL", script)
        self.assertIn("REMOTE_ENTRY_PATH", script)
        self.assertIn("https://github.com/TLMROBIN/HighSchoolPhysics.git", script)
        self.assertIn("REQUIRE_REMOTE_HEAD_MATCH", script)
        self.assertIn("ssh -o BatchMode=yes", script)
        self.assertIn("REQUIRE_CLEAN_WORKTREE", script)
        self.assertIn("REQUIRE_UPSTREAM_PARITY", script)
        self.assertIn("RUN_HTTP_SMOKE", script)
        self.assertIn("HSP_BASE_URL", script)

    def test_remote_release_check_verifies_public_physics_login_entry(self):
        script = Path("scripts/hsp_release_check.sh").read_text(encoding="utf-8")

        self.assertIn("REMOTE_PUBLIC_BASE_URL=\"${REMOTE_PUBLIC_BASE_URL:-http://10.50.159.62}\"", script)
        self.assertIn("REMOTE_ENTRY_PATH=\"${REMOTE_ENTRY_PATH:-/physics/login}\"", script)
        self.assertIn("remote public entry", script)
        self.assertIn("entry_url", script)
        self.assertIn("HTTPError", script)
        self.assertIn("status not in (200, 302, 303, 307, 308)", script)

    def test_remote_auto_update_uses_https_github_and_python_health(self):
        script = Path("scripts/hsp_remote_auto_update.sh").read_text(encoding="utf-8")

        self.assertIn("https://github.com/TLMROBIN/HighSchoolPhysics.git", script)
        self.assertIn("git fetch --prune", script)
        self.assertIn("git reset --hard FETCH_HEAD", script)
        self.assertIn("python3", script)
        self.assertIn("urllib.request", script)

    def test_systemd_timer_runs_remote_auto_update(self):
        service = Path("scripts/systemd/highschoolphysics-auto-update.service").read_text(
            encoding="utf-8"
        )
        timer = Path("scripts/systemd/highschoolphysics-auto-update.timer").read_text(
            encoding="utf-8"
        )

        self.assertIn("hsp_remote_auto_update.sh", service)
        self.assertIn("OnUnitActiveSec=2min", timer)


if __name__ == "__main__":
    unittest.main()
