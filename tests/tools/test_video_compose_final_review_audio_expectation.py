"""Regression tests for audio-aware final review status."""

import shutil
import subprocess

import pytest

from tools.video.video_compose import VideoCompose


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


def _silent_video(path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=2:r=30",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _review(path, metadata):
    _silent_video(path)
    return VideoCompose()._run_final_review(
        path,
        edit_decisions={"metadata": metadata},
    )


def test_explicitly_silent_preview_passes_without_audio(tmp_path):
    review = _review(tmp_path / "silent-preview.mp4", {"audio": "none"})

    assert review["status"] == "pass"
    assert review["recommended_action"] == "present_to_user"
    assert review["checks"]["technical_probe"]["has_audio"] is False
    assert review["checks"]["audio_spotcheck"]["audio_expected"] is False
    assert review["checks"]["audio_spotcheck"]["issues"] == []
    assert review["checks"]["transcript_comparison"]["transcript_matches_script"] is None
    assert not any("audio stream" in issue.lower() for issue in review["issues_found"])


def test_narrated_deliverable_without_audio_is_revise(tmp_path):
    review = _review(tmp_path / "missing-narration.mp4", {"audio": "narration"})

    assert review["status"] == "revise"
    assert review["recommended_action"] == "re_render"
    assert review["checks"]["audio_spotcheck"]["audio_expected"] is True
    assert any(
        "expected audio stream is missing" in issue.lower()
        for issue in review["issues_found"]
    )


def test_explicit_audio_expected_overrides_legacy_silent_metadata(tmp_path):
    review = _review(
        tmp_path / "audio-required.mp4",
        {"audio": "none", "audio_expected": True},
    )

    assert review["status"] == "revise"
    assert review["checks"]["audio_spotcheck"]["audio_expected"] is True



def _narrated_video(path, *, clipped=False):
    audio_filter = "volume=8" if clipped else "volume=0.25"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=2:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-filter:a", audio_filter,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_volume_level_does_not_claim_music_is_present(tmp_path):
    path = tmp_path / "narrated.mp4"
    _narrated_video(path)
    review = VideoCompose()._run_final_review(
        path,
        edit_decisions={"metadata": {"audio_expected": True, "audio": "narration"}},
    )

    audio = review["checks"]["audio_spotcheck"]
    assert audio["music_present"] is False
    assert audio["music_detection_status"] == "not_assessed_volume_metrics_cannot_identify_music"


def test_clipping_warning_escalates_narrated_deliverable_to_revise(tmp_path):
    path = tmp_path / "clipped-narration.mp4"
    _narrated_video(path, clipped=True)
    review = VideoCompose()._run_final_review(
        path,
        edit_decisions={"metadata": {"audio_expected": True, "audio": "narration"}},
    )

    audio = review["checks"]["audio_spotcheck"]
    assert audio["clipping_detected"] is True
    assert review["status"] == "revise"
    assert review["recommended_action"] == "re_render"
