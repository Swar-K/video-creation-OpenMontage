#!/usr/bin/env python3
"""Import an Amazon Video Agent project into a native OpenMontage hybrid workspace.

The source project is never modified. Media is symlinked by default so large
video files are not duplicated. This adapter creates seed artifacts only; it
does not fabricate edit decisions or bypass OpenMontage approval checkpoints.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import wave
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required; install the OpenMontage requirements first.") from exc

from lib.checkpoint import init_project
from lib.paths import PROJECTS_DIR
from schemas.artifacts import validate_artifact

MEDIA_TYPES = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video", ".m4v": "video",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio",
}
SCENE_RE = re.compile(
    r"^\[(?P<id>s\d+)\]\s+type:\s*(?P<type>[^|]+)\|\s*ref:\s*(?P<ref>[^|]+)\|\s*prompt:\s*(?P<prompt>.*)$"
)
SCRIPT_RE = re.compile(r"^\[(?P<id>[^\]]+)\]\s*(?P<body>.*)$")
META_RE = re.compile(r"\s*\|\s*(?:assets|product|callout|p)\s*:", re.IGNORECASE)


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _parse_scenes(path: Path) -> list[dict[str, str]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SCENE_RE.match(line.strip())
        if match:
            item = {key: value.strip() for key, value in match.groupdict().items()}
            raw_ref = item["ref"]
            refs = [] if raw_ref.lower() == "none" else [value.strip() for value in raw_ref.split(";") if value.strip()]
            item["refs"] = refs
            item["ref"] = refs[0] if refs else "none"
            result.append(item)
    return result


def _parse_script(path: Path) -> tuple[str, list[dict[str, str]]]:
    title, label = "", "main"
    sections = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# TITLE:"):
            title = line.partition(":")[2].strip()
            continue
        if line.startswith("## "):
            label = re.sub(r"[^a-zA-Z0-9_-]+", "-", line[3:].strip()).strip("-").lower() or "main"
            continue
        match = SCRIPT_RE.match(line)
        if not match:
            continue
        body = META_RE.split(match.group("body").strip(), maxsplit=1)[0].strip()
        if body:
            sections.append({"id": match.group("id").strip(), "label": label, "text": body})
    if not sections:
        raise ValueError(f"No narration lines found in {path}")
    return title or path.parent.name, sections


def _wav_duration(path: Path) -> float | None:
    if not path.is_file() or path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, OSError, ZeroDivisionError):
        return None


def _validate_project_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,59}", value):
        raise ValueError("project-id must be 3-60 safe letters, digits, hyphens, or underscores")
    return value


def import_project(
    amazon_project: Path,
    project_id: str,
    *,
    projects_dir: Path | None = None,
    media_mode: str = "symlink",
) -> Path:
    source = amazon_project.expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Amazon project directory not found: {source}")
    for name in ("facts.yaml", "script.md", "scenes.md"):
        if not (source / name).is_file():
            raise FileNotFoundError(f"Required source file missing: {source / name}")

    facts = _read_yaml(source / "facts.yaml")
    title, source_sections = _parse_script(source / "script.md")
    source_scenes = _parse_scenes(source / "scenes.md")
    if not source_scenes:
        raise ValueError("No supported [sNN] scene entries found in scenes.md")
    project_id = _validate_project_id(project_id)
    project_root = projects_dir or PROJECTS_DIR
    project_dir = project_root / project_id
    artifact_names = ("brief.json", "script.json", "scene_plan.json", "asset_manifest.json")
    if project_dir.exists():
        # A proposal checkpoint may initialize the canonical workspace before
        # source media is imported. Resume only that explicitly initialized,
        # still-empty workspace; never overwrite a prior import or user assets.
        marker_path = project_dir / "project.json"
        if not marker_path.is_file():
            raise FileExistsError(
                f"OpenMontage project already exists without a resumable project marker: {project_dir}"
            )
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Existing project marker is unreadable: {marker_path}") from exc
        if marker.get("project_id") != project_id or marker.get("pipeline_type") != "hybrid":
            raise ValueError(
                f"Existing workspace marker does not match project {project_id!r} and pipeline 'hybrid'"
            )
        artifacts_dir = project_dir / "artifacts"
        collisions = [name for name in artifact_names if (artifacts_dir / name).exists()]
        if collisions or (project_dir / "amazon_source").exists():
            raise FileExistsError(
                "Refusing to overwrite an existing import: "
                + (", ".join(collisions) if collisions else "amazon_source already exists")
            )
        for folder in ("images", "video", "audio"):
            asset_dir = project_dir / "assets" / folder
            if asset_dir.exists() and any(asset_dir.iterdir()):
                raise FileExistsError(
                    f"Refusing to overwrite existing user/imported media in {asset_dir}"
                )

    project_dir = init_project(
        project_id,
        title=str(facts.get("product_name") or title),
        pipeline_type="hybrid",
        pipeline_dir=projects_dir,
        style_playbook="clean-professional",
    )
    artifacts_dir = project_dir / "artifacts"
    collisions = [name for name in artifact_names if (artifacts_dir / name).exists()]
    if collisions:
        raise FileExistsError("Refusing to overwrite existing artifacts: " + ", ".join(collisions))

    voice_source = source / "out" / "voice.wav"
    voice_duration = _wav_duration(voice_source)
    weights = [max(1, len(item["text"].split())) for item in source_sections]
    total_duration = voice_duration or max(1.0, sum(weights) / 2.35)
    cursor = 0.0
    script_sections = []
    for index, item in enumerate(source_sections):
        end = total_duration if index == len(source_sections) - 1 else cursor + total_duration * weights[index] / sum(weights)
        script_sections.append({
            "id": item["id"], "label": item["label"], "text": item["text"],
            "start_seconds": round(cursor, 3), "end_seconds": round(max(cursor + 0.01, end), 3),
        })
        cursor = end

    scene_plan = []
    scene_to_section = {}
    for index, visual in enumerate(source_scenes):
        section_index = min(
            len(script_sections) - 1,
            round(index * (len(script_sections) - 1) / max(1, len(source_scenes) - 1)),
        )
        section = script_sections[section_index]
        scene_id = visual["id"]
        references = visual.get("refs", [])
        required_assets = []
        has_video_reference = False
        for reference in references:
            if Path(reference).name != reference:
                raise ValueError(f"Scene {scene_id} reference must be a filename inside assets/: {reference!r}")
            reference_path = source / "assets" / reference
            media_type = MEDIA_TYPES.get(reference_path.suffix.lower())
            if media_type not in {"image", "video"} or not reference_path.is_file():
                raise FileNotFoundError(
                    f"Scene {scene_id} references a missing or unsupported existing asset: {reference_path}"
                )
            scene_to_section[reference] = scene_id
            has_video_reference = has_video_reference or media_type == "video"
            required_assets.append({
                "type": media_type,
                "description": reference,
                "source": "provided",
            })

        ai_clip = source / "assets" / f"ai_{scene_id}.mp4"
        if not required_assets and ai_clip.is_file():
            scene_to_section[ai_clip.name] = scene_id
            required_assets.append({
                "type": "video",
                "description": ai_clip.name,
                "source": "provided",
            })
            has_video_reference = True
        elif not required_assets:
            required_assets.append({
                "type": "image",
                "description": visual["prompt"],
                "source": "generate",
            })

        # Anchor each visual beat to its representative narration section;
        # never stretch a short source clip across the full narration timeline.
        scene_start = float(section["start_seconds"])
        scene_end = min(total_duration, scene_start + 5.0)
        scene_plan.append({
            "id": scene_id,
            "type": "broll" if visual["type"].lower() == "mood" or has_video_reference else "generated",
            "description": f"Imported Amazon scene prompt: {visual['prompt']}",
            "start_seconds": round(scene_start, 3),
            "end_seconds": round(max(scene_start + 0.01, scene_end), 3),
            "script_section_id": section["id"],
            "framing": "Reframe deliberately for each approved aspect ratio; keep product details and text in safe areas.",
            "movement": "Retain source clip movement or apply restrained motion to still imagery.",
            "shot_intent": "Use the referenced source media to illustrate the narration without implying unverified product performance.",
            "information_role": "Existing source footage or product imagery; confirm exact cut selection during human review.",
            "required_assets": required_assets,
        })

    brief = {
        "version": "1.0",
        "title": str(facts.get("product_name") or title),
        "hook": script_sections[0]["text"],
        "key_points": [str(x) for x in facts.get("manufacturer_claims", [])[:8]] or [x["text"] for x in script_sections[:5]],
        "core_message": f"Help viewers evaluate {facts.get('product_name') or title} using the supplied product facts and limitations.",
        "cta": script_sections[-1]["text"],
        "tone": "clear, practical, evidence-led product explanation",
        "style": "clean-professional",
        "target_audience": str(facts.get("audience") or "General audience"),
        "target_platform": "youtube",
        "target_duration_seconds": round(total_duration, 3),
        "reference_material": ["amazon_source/facts.yaml", "amazon_source/script.md", "amazon_source/scenes.md"],
        "selected_angle": str(facts.get("angle") or "product overview"),
        "metadata": {"source": "amazon_video_agent", "asin": str(facts.get("asin") or source.name)},
    }
    script_artifact = {
        "version": "1.0", "title": title,
        "total_duration_seconds": round(total_duration, 3),
        "sections": script_sections,
        "metadata": {"source": "amazon_video_agent"},
    }

    media_source = source / "assets"
    if not media_source.is_dir():
        raise FileNotFoundError(f"Amazon assets directory missing: {media_source}")
    media_target = project_dir / "assets"
    manifest_assets = []
    for media in sorted(media_source.iterdir()):
        media_type = MEDIA_TYPES.get(media.suffix.lower())
        if not media.is_file() or not media_type:
            continue
        media_folder = "images" if media_type == "image" else "video" if media_type == "video" else "audio"
        target = media_target / media_folder / media.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Asset target already exists: {target}")
        if media_mode == "symlink":
            target.symlink_to(media.resolve())
        else:
            shutil.copy2(media, target)
        manifest_assets.append({
            "id": re.sub(r"[^A-Za-z0-9_-]+", "-", media.stem).strip("-").lower(),
            "type": media_type, "path": target.relative_to(project_dir).as_posix(),
            "source_tool": "amazon_video_agent_import",
            "scene_id": scene_to_section.get(
                media.name,
                scene_to_section.get(
                    re.sub(r"^ai_(s\d+)\.mp4$", r"\1", media.name),
                    "unassigned",
                ),
            ),
            "format": media.suffix.lower().lstrip("."),
            "subtype": "product_source" if media.name.startswith("product_image_") else "imported_media",
            "generation_summary": "Imported from the Amazon Video Agent project; original file retained.",
        })

    if voice_source.is_file():
        target = media_target / "audio" / voice_source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if media_mode == "symlink":
            target.symlink_to(voice_source.resolve())
        else:
            shutil.copy2(voice_source, target)
        manifest_assets.append({
            "id": "amazon-narration", "type": "narration",
            "path": target.relative_to(project_dir).as_posix(),
            "source_tool": "amazon_video_agent_import",
            "scene_id": source_scenes[0]["id"],
            "format": "wav",
            "duration_seconds": round(voice_duration, 3) if voice_duration else round(total_duration, 3),
            "generation_summary": "Reused existing Amazon narration; no TTS provider or paid API invoked.",
        })

    manifest = {
        "version": "1.0", "assets": manifest_assets,
        "metadata": {
            "source": "amazon_video_agent", "media_mode": media_mode,
            "narration_imported": voice_source.is_file(),
            "note": "Importer intentionally does not create edit decisions or mark gated stages approved.",
        },
    }
    payloads = {
        "brief": brief,
        "script": script_artifact,
        "scene_plan": {"version": "1.0", "style_playbook": "clean-professional", "scenes": scene_plan,
                       "metadata": {"source": "amazon_video_agent"}},
        "asset_manifest": manifest,
    }
    for artifact_name, payload in payloads.items():
        validate_artifact(artifact_name, payload)
    for artifact_name, payload in payloads.items():
        (artifacts_dir / f"{artifact_name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (project_dir / "amazon_source").symlink_to(source, target_is_directory=True)
    return project_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amazon-project", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--projects-dir", type=Path, default=None)
    parser.add_argument("--media-mode", choices=("symlink", "copy"), default="symlink")
    args = parser.parse_args(argv)
    try:
        project = import_project(args.amazon_project, args.project_id, projects_dir=args.projects_dir, media_mode=args.media_mode)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "success": True, "project_dir": str(project),
        "artifacts": [f"artifacts/{name}" for name in ("brief.json", "script.json", "scene_plan.json", "asset_manifest.json")],
        "next_step": "Review imported artifacts, then continue through OpenMontage's hybrid approval/checkpoint workflow.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
