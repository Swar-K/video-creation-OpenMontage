"""Render an imported Ad Agent project with Ad Agent's native OpenCV renderer.

The Ad Agent checkout is imported read-only. This adapter consumes the
OpenMontage importer's staged media and approved narration, then delegates
frame composition to the original Ad Agent Shot/render_video implementation.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _probe_dimensions(path: Path, kind: str) -> tuple[int, int]:
    if kind == "image":
        import cv2
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Cannot decode image asset: {path}")
        return int(image.shape[1]), int(image.shape[0])
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if width < 1 or height < 1:
        raise ValueError(f"Cannot read video dimensions: {path}")
    return width, height


def _asset_for_section(section: dict[str, Any], scene: dict[str, Any],
                       assets: list[dict[str, Any]], fallback_index: int) -> dict[str, Any]:
    refs = scene.get("required_assets", [])
    names = [Path(str(ref.get("description", ""))).name for ref in refs
             if ref.get("type") in {"image", "video"}]
    for name in names:
        found = next((item for item in assets
                      if Path(item["path"]).name == name
                      and item.get("type") in {"image", "video"}), None)
        if found:
            return found
    usable = [item for item in assets if item.get("type") in {"image", "video"}]
    if not usable:
        raise ValueError("Imported project contains no usable image/video assets")
    return usable[fallback_index % len(usable)]


def render_with_ad_agent(*, ad_agent_root: Path, project_root: Path,
                         script: dict[str, Any], manifest: dict[str, Any],
                         plan: dict[str, Any], output_path: Path,
                         narration_audio: Path, final_output: Path,
                         script_text: str, edit_decisions: dict[str, Any]) -> dict[str, Any]:
    """Render silent frames with Ad Agent, mux approved narration, and review."""
    root = Path(ad_agent_root).resolve()
    if not (root / "adagent" / "render.py").is_file():
        raise FileNotFoundError(f"Ad Agent checkout not found at {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # Import only the native renderer's modules; no Ad Agent project/config writes.
    native = importlib.import_module("adagent.render")
    overlays = importlib.import_module("adagent.overlay")
    import cv2

    project = Path(project_root).resolve()
    assets = []
    for item in manifest.get("assets", []):
        if item.get("type") not in {"image", "video"}:
            continue
        path = Path(item["path"]).resolve()
        try:
            path.relative_to(project)
        except ValueError as exc:
            raise ValueError(f"Asset path escapes imported workspace: {path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Imported asset missing: {path}")
        width, height = _probe_dimensions(path, item["type"])
        assets.append({
            "id": item["id"], "kind": item["type"], "file": path.name,
            "path": path, "width": width, "height": height, "usable": True,
            "description": path.stem,
        })
    if not assets:
        raise ValueError("No usable visual assets available to Ad Agent renderer")

    width, height, fps = 1920, 1080, 30
    tpl = {
        "accent": (45, 130, 220),
        "caption": {"style": "pill"},
        "grade": {"contrast": 1.0, "brightness": 0.0, "saturation": 1.0,
                  "tint": [0, 0, 0], "vignette": 0.0, "grain": 0.0},
        "transition": {"kind": "fade", "seconds": 0.25},
        "backdrop": "studio", "badge": "solid",
        "endcard": {"bg": [8, 9, 12, 220], "text": [255, 255, 255]},
        "letterbox": 0.0,
    }
    cfg = {"video": {"width": width, "height": height, "fps": fps,
                     "crossfade": 0.25, "preset": "veryfast", "crf": 21,
                     "fonts": []}, "_tpl": tpl}
    scenes = {item["script_section_id"]: item for item in plan.get("scenes", [])}
    sections = script.get("sections", [])
    total = float(script.get("total_duration_seconds") or 0)
    if total <= 0 or not sections:
        raise ValueError("Imported script has no valid timed sections")

    shots = []
    caption_overlays = []
    for index, section in enumerate(sections):
        start = float(section["start_seconds"])
        end = float(section["end_seconds"])
        if end <= start:
            continue
        asset = _asset_for_section(section, scenes.get(section["id"], {}),
                                   assets, index)
        move = {"z": (1.0, 1.0), "c": ((0.5, 0.5), (0.5, 0.5))}
        shots.append(native.Shot(
            start=start, dur=end-start, asset=asset, path=Path(asset["path"]),
            W=width, H=height, move=move, clip_in=0.0, speed=1.0,
        ))
        chunks = overlays.split_caption(section["text"], max_words=9)
        total_chars = max(1, sum(len(chunk) for chunk in chunks))
        cursor = start
        for chunk in chunks:
            span = (end-start) * len(chunk) / total_chars
            caption_overlays.extend(overlays.caption_overlays(
                chunk, overlays.load_font(cfg, int(height * 0.048)),
                width, height, tpl, cursor, min(end, cursor+span+0.12),
            ))
            cursor += span
    if not shots:
        raise ValueError("No timed visual shots could be constructed")
    shots.sort(key=lambda item: item.start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_path.with_suffix(".adagent-render.log")
    native.render_video(shots, caption_overlays, total, cfg, output_path, log_path)

    final_output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(output_path), "-i", str(narration_audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-movflags", "+faststart", str(final_output),
    ], check=True, capture_output=True, timeout=300)
    review_decisions = json.loads(json.dumps(edit_decisions))
    review_decisions["render_runtime"] = "adagent"
    review_decisions.setdefault("metadata", {})["audio_expected"] = True
    final_review = VideoComposeForReview()._run_final_review(
        final_output, review_decisions, script_text=script_text
    )
    if final_review.get("status") != "pass":
        raise RuntimeError("Ad Agent Native final review failed: " + str(final_review))
    return {
        "native_renderer": "adagent.render.render_video",
        "render_runtime": "adagent",
        "visual_shots": len(shots),
        "render_log": str(log_path),
        "final_review": final_review,
    }


def VideoComposeForReview():
    """Lazy import avoids importing OpenMontage composition until review is needed."""
    from tools.video.video_compose import VideoCompose
    return VideoCompose()
