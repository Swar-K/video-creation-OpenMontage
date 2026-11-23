from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_piper_narrated_project import text_from

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render_piper_narrated_project.py"
WORKFLOW = ROOT / ".github" / "workflows" / "alien-draft-seal-narrated.yml"


class PiperNarratedProjectTests(unittest.TestCase):
    def test_extracts_narration_without_inline_product_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "script.md"
            source.write_text(
                "# TITLE: Example\n## hook\n[1] First narration line. | p: 1\n"
                "## outro\n[O1] Final narration line. | product: 1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                text_from(source),
                "First narration line. Final narration line.",
            )

    def test_cli_fails_closed_without_both_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--source-project", str(base / "B08S3M2HKL"),
                    "--model", str(base / "voice.onnx"),
                    "--work-dir", str(base / "work"),
                    "--output", str(base / "out.mp4"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("both explicit approvals are required", result.stderr)
            self.assertFalse((base / "out.mp4").exists())

    def test_workflow_requires_two_explicit_false_by_default_inputs(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("confirm_tts_generation:", workflow)
        self.assertIn("confirm_full_render:", workflow)
        self.assertEqual(workflow.count("default: false"), 2)
        self.assertIn("needs: safety-gate", workflow)
        self.assertIn("github.event.inputs.confirm_tts_generation == 'true'", workflow)
        self.assertIn("github.event.inputs.confirm_full_render == 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
