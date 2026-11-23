#!/usr/bin/env python3
"""Approval-gated Ad Agent voice narration and full-length Remotion render."""
import argparse,json,re,shutil,subprocess,sys,wave
from pathlib import Path

# When executed as `python scripts/...py`, Python places scripts/ rather than
# the repository root on sys.path. Add the root before importing project modules.
REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))

from integrations.amazon_video_agent.importer import _parse_script
from integrations.amazon_video_agent.list_adapter import import_selected_project
from tools.video.video_compose import VideoCompose
from schemas.artifacts import validate_artifact
MODEL="en_US-ljspeech-medium"
REMOTION_PROCESS_TIMEOUT_SECONDS=1800
REMOTION_CONCURRENCY=2

def clean_source_video_filter(filename):
    """Normalize supplied footage without product-specific visual assumptions."""
    return (
        "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


def prepare_clean_video_assets(project_root, manifest):
    """Create silent full-HD derivatives without mutating source media."""
    root = Path(project_root).resolve()
    cleaned = json.loads(json.dumps(manifest))
    processed = []
    for asset in cleaned.get("assets", []):
        if asset.get("type") != "video":
            continue
        source = Path(asset["path"]).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Video asset path escapes isolated project workspace: {source}") from exc
        if not source.is_file():
            raise FileNotFoundError(f"Video source missing before normalization: {source}")
        target_dir = root / "assets" / "video_normalized"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}.normalized.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-vf", clean_source_video_filter(source.name), "-an", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(target)],
            check=True, capture_output=True, timeout=900,
        )
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(f"Normalized video asset was not created: {target}")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
             "-of", "json", str(target)], check=True, capture_output=True, text=True, timeout=30,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        video = next((x for x in streams if x.get("codec_type") == "video"), None)
        if not video or (video.get("width"), video.get("height")) != (1920, 1080):
            raise RuntimeError(f"Normalized video is not 1920x1080: {target}")
        if any(x.get("codec_type") == "audio" for x in streams):
            raise RuntimeError(f"Normalized source video unexpectedly contains audio: {target}")
        asset["path"] = str(target.resolve())
        processed.append(source.name)
    return cleaned, processed


def approved_product_stills(manifest, plan=None):
    """Prefer scene-referenced supplied images; otherwise use supplied images."""
    images = [x for x in manifest.get("assets", []) if x.get("type") == "image"]
    if not images:
        raise RuntimeError("Selected project has no supplied image assets")
    by_name = {Path(x["path"]).name: x for x in images}
    selected = []
    if plan:
        for scene in plan.get("scenes", []):
            for ref in scene.get("required_assets", []):
                if ref.get("type") == "image":
                    match = by_name.get(Path(ref.get("description", "")).name)
                    if match and match not in selected:
                        selected.append(match)
    return selected or images


def remotion_render_options():
    """Keep full-length renders on native Remotion with an explicit process limit."""
    return {
        "remotion_process_timeout_seconds": REMOTION_PROCESS_TIMEOUT_SECONDS,
        "remotion_concurrency": REMOTION_CONCURRENCY,
    }

def limit_narration_peaks(source, destination):
    """Create a peak-limited narration WAV before it is muxed into the video.

    A -2 dBFS ceiling leaves headroom for AAC encoding and avoids relying on
    the final-review checker to discover clipped source peaks after rendering.
    """
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([
        "ffmpeg","-hide_banner","-loglevel","error","-y",
        "-i",str(source),
        "-af","alimiter=limit=0.794:level=disabled",
        "-c:a","pcm_s16le",str(destination),
    ],check=True,capture_output=True,timeout=180)
    if not destination.is_file() or destination.stat().st_size<1000:
        raise RuntimeError("Peak-limited narration WAV was not created")
    return destination


def text_from(path):
    _,sections=_parse_script(path)
    text=" ".join(s["text"] for s in sections)
    if not text.strip(): raise ValueError("No narration in script")
    return text
def build_edit_decisions(script,manifest,plan,stills,project_id,voice_provider="piper",voice_model=MODEL):
    scenes={x["script_section_id"]:x for x in plan["scenes"]}
    cuts=[]; overlays=[]
    for i,s in enumerate(script["sections"]):
        start,end=float(s["start_seconds"]),float(s["end_seconds"])
        if end<=start: continue
        scene=scenes.get(s["id"],{})
        refs=scene.get("required_assets",[])
        clip=None
        for ref in refs:
            if ref.get("type")=="video":
                clip=next((x for x in manifest["assets"] if x["type"]=="video" and Path(x["path"]).name==Path(ref["description"]).name),None)
                if clip: break
        span=end-start; clip_span=min(4,span*.42) if clip else 0
        if clip_span>=1:
            cuts.append({"id":s["id"]+"-video","source":clip["id"],"in_seconds":round(start,3),"out_seconds":round(start+clip_span,3),"layer":"primary","transition_in":"cut","transition_out":"cut"})
        still=None
        for ref in refs:
            if ref.get("type")=="image":
                still=next((x for x in manifest["assets"] if x["type"]=="image" and Path(x["path"]).name==Path(ref["description"]).name),None)
                if still: break
        if still is None:
            still=stills[i%len(stills)]
        cuts.append({"id":s["id"]+"-still","source":still["id"],"in_seconds":round(start+clip_span,3),"out_seconds":round(end,3),"layer":"primary","transition_in":"cut","transition_out":"cut","transform":{"animation":"static"},"image_fit":"contain","image_vignette":False,"backgroundColor":"#FFFFFF"})
        label=s["label"].replace("-"," ").replace("_"," ").title()
        rank_match=re.fullmatch(r"item_(\d+)",s["label"],re.I)
        if rank_match:
            label=f"Rank #{rank_match.group(1)}"
        overlays.append({"type":"section_title","in_seconds":round(start,3),"out_seconds":round(min(end,start+2.4),3),"text":label,"position":"top-center"})
    return {"version":"1.0","tailPaddingSeconds":0,"cuts":cuts,"overlays":overlays,"renderer_family":"product-reveal","render_runtime":"remotion","composition_mode":"templated","metadata":{"project_id":"amazon-"+project_id,"deliverable":"full_narrated_video","duration_seconds":script["total_duration_seconds"],"aspect_ratio":"16:9","fps":30,"audio":"narration","audio_expected":True,"voice_provider":voice_provider,"voice_model":voice_model,"voice_fallback":False,"music_enabled":False,"external_assets_allowed":False,"generated_assets_allowed":False,"source_policy":"existing_supplied_media_only","notes":["Separate full-length edit; preview edit_decisions.json is not read or changed.","Only supplied project images and video are used.","Source video is normalized to 1080p without product-specific cropping or overlay removal."]}}


def manifest_with_absolute_paths(project_root, manifest):
    """Resolve imported project-relative asset paths for Remotion's media stager.

    The importer intentionally stores portable paths such as
    assets/video/clip.mp4. Remotion's stager receives the repository as its
    working directory, so those paths must be anchored to this isolated
    project before _stage_remotion_media can copy them into public/.
    """
    root=Path(project_root).resolve()
    resolved=json.loads(json.dumps(manifest))
    for asset in resolved.get("assets",[]):
        raw=Path(asset["path"])
        candidate=(root/raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Asset path escapes isolated project workspace: {asset['path']}") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"Imported asset is missing before Remotion staging: {candidate}")
        asset["path"]=str(candidate)
    return resolved


EDGE_VOICES = {
    "edge_female": ("en-US-AvaMultilingualNeural", "female"),
    "edge_female_2": ("en-US-EmmaMultilingualNeural", "female"),
    "edge_male": ("en-US-AndrewMultilingualNeural", "male"),
    "edge_male_2": ("en-US-BrianMultilingualNeural", "male"),
}


def synthesize_edge_narration(text, voice_name, output_wav):
    """Use one selected Microsoft Edge neural voice for the entire narration; never switch voices."""
    import asyncio
    import edge_tts

    output_wav=Path(output_wav)
    output_wav.parent.mkdir(parents=True,exist_ok=True)
    mp3=output_wav.with_suffix(".edge.mp3")

    async def _save():
        communicator=edge_tts.Communicate(text=text,voice=voice_name,rate="-4%",volume="+0%")
        await communicator.save(str(mp3))

    try:
        asyncio.run(_save())
        subprocess.run([
            "ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(mp3),
            "-ar","22050","-ac","1","-c:a","pcm_s16le",str(output_wav)
        ],check=True,capture_output=True,timeout=180)
    finally:
        mp3.unlink(missing_ok=True)
    if not output_wav.is_file() or output_wav.stat().st_size<1000:
        raise RuntimeError(f"Edge TTS did not produce valid narration for {voice_name}")
    return output_wav


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source-project",type=Path,required=True)
    p.add_argument("--model",type=Path,default=None)
    p.add_argument("--work-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--voice",choices=["piper_female",*EDGE_VOICES],default="piper_female")
    p.add_argument("--approve-tts",action="store_true")
    p.add_argument("--approve-render",action="store_true")
    a=p.parse_args()
    if not(a.approve_tts and a.approve_render):
        raise SystemExit("Blocked: both explicit approvals are required")
    source=a.source_project.expanduser().resolve()
    project_id=source.name
    if not project_id or not all(c.isalnum() or c in "-_" for c in project_id):
        raise SystemExit("Project directory name must contain only letters, digits, hyphens, or underscores")
    if not source.is_dir():
        raise FileNotFoundError(f"Project directory not found: {source}")
    is_list=(source/"list.yaml").is_file()
    required=("list.yaml","script.md") if is_list else ("facts.yaml","script.md","scenes.md","assets")
    for name in required:
        if not (source/name).exists():
            raise FileNotFoundError(f"Selected project is missing required input: {name}")
    narration=text_from(source/"script.md")
    audio_dir=a.work_dir/"narration"
    audio_dir.mkdir(parents=True,exist_ok=True)
    audio=audio_dir/f"{project_id}.wav"

    if a.voice=="piper_female":
        if a.model is None or not a.model.is_file() or not Path(str(a.model)+".json").is_file() or not shutil.which("piper"):
            raise RuntimeError("Pinned Piper runtime/model unavailable; no fallback")
        proc=subprocess.run(
            ["piper","--model",str(a.model),"--output_file",str(audio),"--length-scale","1.0","--sentence-silence","0.25"],
            input=narration,text=True,encoding="utf-8",capture_output=True,timeout=900
        )
        if proc.returncode:
            raise RuntimeError("Piper failed: "+proc.stderr[-2000:])
        voice_provider="piper"
        voice_model=MODEL
        voice_gender="female"
        voice_warning=None
    else:
        voice_name,voice_gender=EDGE_VOICES[a.voice]
        synthesize_edge_narration(narration,voice_name,audio)
        voice_provider="microsoft_edge_tts"
        voice_model=voice_name
        voice_warning="Uses Microsoft's unofficial Edge TTS endpoint; Ad Agent notes there is no commercial licence statement."
    with wave.open(str(audio),"rb") as w:
        duration=w.getnframes()/w.getframerate()
        if duration<30:
            raise RuntimeError("Narration duration invalid")

    project=import_selected_project(
        source,"amazon-"+project_id+"-narrated",projects_dir=a.work_dir,media_mode="copy",
        narration_audio=audio
    )
    root=project/"artifacts"
    script=json.loads((root/"script.json").read_text())
    manifest=json.loads((root/"asset_manifest.json").read_text())
    plan=json.loads((root/"scene_plan.json").read_text())
    stills=approved_product_stills(manifest,plan)
    decisions=build_edit_decisions(
        script,manifest,plan,stills,project_id,
        voice_provider=voice_provider,voice_model=voice_model
    )
    validate_artifact("edit_decisions",decisions)
    render_manifest=manifest_with_absolute_paths(project,manifest)
    render_manifest,cleaned_video_assets=prepare_clean_video_assets(project,render_manifest)
    source_audio=project/"assets"/"audio"/"voice.wav"
    if not source_audio.is_file():
        raise RuntimeError("Imported narration WAV missing from isolated project workspace")
    audio_for_render=limit_narration_peaks(
        source_audio,project/"assets"/"audio"/"voice-limited.wav"
    )
    (root/"narrated_edit_decisions.json").write_text(json.dumps(decisions,indent=2)+"\n")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    result=VideoCompose().execute({
        "operation":"render","edit_decisions":decisions,"asset_manifest":render_manifest,
        "audio_path":str(audio_for_render.resolve()),"output_path":str(a.output.resolve()),
        "profile":"youtube_landscape","script_text":narration,**remotion_render_options()
    })
    if not result.success:
        raise RuntimeError("Remotion render failed: "+str(result.error))
    if not a.output.is_file() or a.output.stat().st_size<100000:
        raise RuntimeError("MP4 missing/invalid")
    final_review=(result.data or {}).get("final_review")
    if not isinstance(final_review,dict) or final_review.get("status")!="pass":
        raise RuntimeError("Final review did not pass: "+str(final_review))
    report={
        "provider":voice_provider,"voice":voice_model,"gender":voice_gender,
        "fallback":False,"warning":voice_warning,
        "audio_duration_seconds":round(duration,2),"visual_cuts":len(decisions["cuts"]),
        "staged_assets":len(render_manifest["assets"]),
        "remotion_process_timeout_seconds":REMOTION_PROCESS_TIMEOUT_SECONDS,
        "remotion_concurrency":REMOTION_CONCURRENCY,"audio_peak_limit_dbfs":-2.0,
        "audio_source":f"{voice_provider} narration only; source-video audio is not muxed",
        "visual_asset_cleanup":{
            "processed_video_assets":len(cleaned_video_assets),
            "product_specific_overlays_removed":False,
            "normalization":"fit-within-1920x1080; preserve full source frame",
            "selected_still_images":[Path(x["path"]).name for x in stills],
            "still_image_fit":"contain"
        },
        "final_review":final_review
    }
    a.output.with_suffix(".report.json").write_text(json.dumps(report,indent=2,default=str)+"\n")


if __name__=="__main__":
    main()
