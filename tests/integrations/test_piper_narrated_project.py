from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.render_piper_narrated_project import (
    REMOTION_CONCURRENCY,
    REMOTION_PROCESS_TIMEOUT_SECONDS,
    approved_product_stills,
    build_edit_decisions,
    clean_source_video_filter,
    manifest_with_absolute_paths,
    prepare_clean_video_assets,
    limit_narration_peaks,
    remotion_render_options,
    text_from,
)
from schemas.artifacts import validate_artifact
from tools.video.video_compose import VideoCompose

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render_piper_narrated_project.py"
WORKFLOW = ROOT / ".github" / "workflows" / "alien-draft-seal-narrated.yml"


class PiperNarratedProjectTests(unittest.TestCase):
    def test_full_render_uses_explicit_wall_clock_timeout_without_changing_browser_timeout(self):
        self.assertEqual(REMOTION_PROCESS_TIMEOUT_SECONDS, 1800)
        self.assertEqual(REMOTION_CONCURRENCY, 2)
        self.assertEqual(
            remotion_render_options(),
            {
                "remotion_process_timeout_seconds": 1800,
                "remotion_concurrency": 2,
            },
        )
        self.assertEqual(
            VideoCompose._resolve_remotion_process_timeout(30, 1800),
            1800,
        )
        self.assertEqual(VideoCompose._resolve_remotion_process_timeout(50), 750)
        with self.assertRaisesRegex(ValueError, "must be at least 1"):
            VideoCompose._resolve_remotion_process_timeout(10, 0)

    def test_limiter_creates_headroom_for_piper_audio(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "voice.wav"
            limited = Path(tmp) / "voice-limited.wav"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-af", "volume=8", "-c:a", "pcm_s16le", str(source),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(limit_narration_peaks(source, limited), limited)
            probe = subprocess.run(
                ["ffmpeg", "-i", str(limited), "-af", "volumedetect", "-f", "null", "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            max_line = next(
                line for line in probe.stderr.splitlines() if "max_volume:" in line
            )
            peak = float(max_line.split("max_volume:")[1].strip().split()[0])
            self.assertLessEqual(peak, -1.5)

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
        self.assertEqual(decisions["cuts"][1]["image_fit"], "contain")
        self.assertFalse(decisions["cuts"][1]["image_vignette"])
        self.assertEqual(decisions["cuts"][1]["backgroundColor"], "#FFFFFF")
        self.assertEqual(decisions["overlays"][0]["position"], "top-center")

    def test_video_cleanup_filter_removes_player_controls_and_targeted_overlays(self):
        affected = clean_source_video_filter("2026-10-23 17-27-34 - Trim7.mp4")
        self.assertIn("delogo=x=1010:y=620:w=250:h=55", affected)
        self.assertIn("delogo=x=345:y=375:w=30:h=35", affected)
        self.assertIn("crop=iw:ih-28:0:0", affected)
        self.assertIn("pad=1920:1080", affected)
        self.assertNotIn("delogo", clean_source_video_filter("2026-10-23 17-27-34 - Trim.mp4"))

    def test_only_neutral_product_stills_are_selected(self):
        manifest = {
            "assets": [
                {"type": "image", "path": "assets/images/product_image_01.jpg"},
                {"type": "image", "path": "assets/images/product_image_31.jpg"},
                {"type": "image", "path": "assets/images/product_image_51.jpg"},
                {"type": "image", "path": "assets/images/product_image_61.jpg"},
                {"type": "image", "path": "assets/images/product_image_81.jpg"},
            ]
        }
        selected = approved_product_stills(manifest)
        self.assertEqual(
            {Path(asset["path"]).name for asset in selected},
            {"product_image_51.jpg", "product_image_81.jpg"},
        )

    def test_video_cleanup_outputs_silent_full_hd_derivative_without_mutating_source(self):
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            self.skipTest("ffmpeg and ffprobe are required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "assets" / "video" / "2026-10-23 17-27-34 - Trim7.mp4"
            source.parent.mkdir(parents=True)
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=30:d=0.4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(source),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            original_manifest = {
                "assets": [{"id": "door", "type": "video", "path": str(source)}]
            }
            cleaned_manifest, processed = prepare_clean_video_assets(root, original_manifest)
            cleaned_path = Path(cleaned_manifest["assets"][0]["path"])
            self.assertEqual(processed, [source.name])
            self.assertTrue(cleaned_path.is_file())
            self.assertEqual(original_manifest["assets"][0]["path"], str(source))
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type,width,height", "-of", "json", str(cleaned_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            streams = json.loads(probe.stdout)["streams"]
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1920, 1080))
            self.assertFalse(any(stream["codec_type"] == "audio" for stream in streams))

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
