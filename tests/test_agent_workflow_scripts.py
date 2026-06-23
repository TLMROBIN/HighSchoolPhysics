import unittest
from pathlib import Path


class AgentWorkflowScriptTests(unittest.TestCase):
    def test_agents_contract_requires_remote_release_gate_and_recovery_probe(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("scripts/hsp_release_check.sh", text)
        self.assertIn("REQUIRE_REMOTE_HEAD_MATCH=1", text)
        self.assertIn("scripts/hsp_recover_context.sh", text)
        self.assertIn("只读恢复现场", text)

    def test_recover_context_script_checks_local_remote_timer_and_service(self):
        script = Path("scripts/hsp_recover_context.sh").read_text(encoding="utf-8")

        self.assertIn("git status --short --branch", script)
        self.assertIn("git worktree list", script)
        self.assertIn("ssh -o BatchMode=yes", script)
        self.assertIn("/home/yub/Documents/trae_projects/HighSchoolPhysics", script)
        self.assertIn("systemctl --user", script)
        self.assertIn("highschoolphysics-auto-update.timer", script)
        self.assertIn("python3 -m highschoolphysics.server", script)


if __name__ == "__main__":
    unittest.main()
