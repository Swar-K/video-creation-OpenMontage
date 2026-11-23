from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_piper_narrated_project import (
    build_edit_decisions,
    manifest_with_absolute_paths,
    text_from,
)
from schemas.artifacts import validate_artifact

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

    def test_generated_edit_decisions_match_schema(self):
        script = {
            "total_duration_seconds": 20.0,
            "sections": [
                {"id": "s01", "label": "product-demo", "start_seconds": 0.0, "end_seconds": 20.0}
            ],
        }
        manifest = {
            "assets": [
                {"id": "clip1", "type": "video", "path": "assets/video/door.mp4"},
                {"id": "still1", "type": "image", "path": "assets/images/product.jpg"},
            ]
        }
        plan = {
            "scenes": [
                {
                    "script_section_id": "s01",
                    "required_assets": [{"type": "video", "description": "door.mp4"}],
                }
            ]
        }
        decisions = build_edit_decisions(
            script, manifest, plan, [manifest["assets"][1]]
        )
        validate_artifact("edit_decisions", decisions)
        self.assertEqual(decisions["cuts"][1]["transform"]["animation"], "subtle-zoom-in")
        self.assertEqual(decisions["overlays"][0]["position"], "top-center")

    def test_manifest_paths_resolve_inside_project_and_preserve_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            media = project / "assets" / "video" / "door trim clip.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"fixture")
            original = {
                "assets": [
                    {"id": "door-clip", "type": "video", "path": "assets/video/door trim clip.mp4"}
                ]
            }
            resolved = manifest_with_absolute_paths(project, original)
            self.assertEqual(resolved["assets"][0]["path"], str(media.resolve()))
            self.assertTrue(Path(resolved["assets"][0]["path"]).is_file())
            self.assertEqual(original["assets"][0]["path"], "assets/video/door trim clip.mp4")

    def test_manifest_path_missing_fails_before_remotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "missing before Remotion staging"):
                manifest_with_absolute_paths(
                    Path(tmp),
                    {"assets": [{"id": "missing", "type": "video", "path": "assets/video/missing.mp4"}]},
                )

    def test_manifest_path_cannot_escape_project_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "escapes isolated project workspace"):
                manifest_with_absolute_paths(
                    Path(tmp),
                    {"assets": [{"id": "outside", "type": "video", "path": "../outside.mp4"}]},
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
