from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_piper_narrated_project import (
    MODEL, REMOTION_CONCURRENCY, REMOTION_PROCESS_TIMEOUT_SECONDS,
    approved_product_stills, build_edit_decisions, clean_source_video_filter,
    limit_narration_peaks, manifest_with_absolute_paths, prepare_clean_video_assets,
    remotion_render_options, text_from,
)
from schemas.artifacts import validate_artifact

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render_piper_narrated_project.py"
WORKFLOW = ROOT / ".github" / "workflows" / "amazon-video-agent-narrated.yml"


class PiperNarratedProjectTests(unittest.TestCase):
    def test_full_render_timeout_settings(self):
        self.assertEqual(REMOTION_PROCESS_TIMEOUT_SECONDS, 1800)
        self.assertEqual(REMOTION_CONCURRENCY, 2)
        self.assertEqual(remotion_render_options(), {
            "remotion_process_timeout_seconds": 1800,
            "remotion_concurrency": 2,
        })

    def test_extracts_narration_without_inline_product_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "script.md"
            source.write_text(
                "# TITLE: Example\n## hook\n[1] First narration line. | p: 1\n"
                "## outro\n[O1] Final narration line. | product: 1\n",
                encoding="utf-8",
            )
            self.assertEqual(text_from(source), "First narration line. Final narration line.")

    def test_edit_decisions_use_dynamic_project_id_and_schema(self):
        script = {
            "total_duration_seconds": 20.0,
            "sections": [{"id": "s01", "label": "product-demo",
                          "start_seconds": 0.0, "end_seconds": 20.0}],
        }
        manifest = {"assets": [
            {"id": "clip1", "type": "video", "path": "assets/video/door.mp4"},
            {"id": "still1", "type": "image", "path": "assets/images/product.jpg"},
        ]}
        plan = {"scenes": [{"script_section_id": "s01",
                            "required_assets": [{"type": "video", "description": "door.mp4"}]}]}
        decisions = build_edit_decisions(script, manifest, plan, [manifest["assets"][1]], "OTHERASIN")
        validate_artifact("edit_decisions", decisions)
        self.assertEqual(decisions["metadata"]["project_id"], "amazon-OTHERASIN")
        self.assertEqual(decisions["cuts"][1]["transform"]["animation"], "static")
        self.assertEqual(decisions["cuts"][1]["image_fit"], "contain")

    def test_visual_normalization_does_not_apply_alien_specific_crop_or_masks(self):
        filter_text = clean_source_video_filter("any-product-clip.mp4")
        self.assertIn("force_original_aspect_ratio=decrease", filter_text)
        self.assertIn("pad=1920:1080", filter_text)
        self.assertNotIn("delogo", filter_text)
        self.assertNotIn("crop=", filter_text)

    def test_selects_scene_referenced_images_or_generic_project_images(self):
        manifest = {"assets": [
            {"id": "img1", "type": "image", "path": "assets/images/one.jpg"},
            {"id": "img2", "type": "image", "path": "assets/images/two.jpg"},
        ]}
        plan = {"scenes": [{"required_assets": [
            {"type": "image", "description": "two.jpg"}
        ]}]}
        self.assertEqual(approved_product_stills(manifest, plan), [manifest["assets"][1]])
        self.assertEqual(approved_product_stills(manifest), manifest["assets"])

    def test_limiter_creates_headroom_for_piper_audio(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "voice.wav"
            limited = Path(tmp) / "voice-limited.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-af", "volume=8", "-c:a", "pcm_s16le", str(source),
            ], check=True, capture_output=True, timeout=30)
            limit_narration_peaks(source, limited)
            self.assertTrue(limited.is_file())

    def test_manifest_paths_resolve_inside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            media = project / "assets" / "video" / "clip.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"fixture")
            resolved = manifest_with_absolute_paths(project, {"assets": [
                {"id": "clip", "type": "video", "path": "assets/video/clip.mp4"}
            ]})
            self.assertEqual(resolved["assets"][0]["path"], str(media.resolve()))

    def test_cli_fails_closed_without_both_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--source-project", str(base / "OTHERASIN"),
                "--model", str(base / "voice.onnx"), "--work-dir", str(base / "work"),
                "--output", str(base / "out.mp4"),
            ], capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("both explicit approvals are required", result.stderr)
            self.assertFalse((base / "out.mp4").exists())

    def test_workflow_is_project_selectable_and_keeps_two_false_by_default_approvals(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("project_id:", workflow)
        self.assertIn("inputs.project_id", workflow)
        self.assertIn("confirm_tts_generation:", workflow)
        self.assertIn("confirm_full_render:", workflow)
        self.assertEqual(workflow.count("default: false"), 2)
        self.assertIn("needs: safety-gate", workflow)
        self.assertIn("inputs.confirm_tts_generation == true", workflow)
        self.assertIn("inputs.confirm_full_render == true", workflow)

    def test_workflow_uses_one_project_specific_output_for_render_validation_and_upload(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('OUTPUT="out/$PROJECT_ID-full-narrated.mp4"', workflow)
        self.assertIn('ffprobe -v error', workflow)
        self.assertIn('ffmpeg -v error -i "$OUTPUT"', workflow)
        self.assertIn('openmontage/out/${{ inputs.project_id }}-full-narrated.mp4', workflow)
        self.assertIn('openmontage/out/${{ inputs.project_id }}-full-narrated.report.json', workflow)
        self.assertNotIn("alien-draft-seal-full-narrated.mp4", workflow)

    def test_project_id_is_passed_via_environment_not_interpolated_into_shell(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("PROJECT_ID: ${{ inputs.project_id }}", workflow)
        self.assertIn('[[ ! "$PROJECT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{2,59}$ ]]', workflow)
        self.assertNotIn('${{ github.event.inputs.project_id }}', workflow)
        self.assertIn('"../amazon-video-agent/projects/$PROJECT_ID"', workflow)


if __name__ == "__main__":
    unittest.main()
