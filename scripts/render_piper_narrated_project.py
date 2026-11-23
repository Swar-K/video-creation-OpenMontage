#!/usr/bin/env python3
"""Approval-gated Piper female narration and full-length Remotion render."""
import argparse,json,shutil,subprocess,sys,wave
from pathlib import Path

# When executed as `python scripts/...py`, Python places scripts/ rather than
# the repository root on sys.path. Add the root before importing project modules.
REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))

from integrations.amazon_video_agent.importer import import_project,_parse_script
from tools.video.video_compose import VideoCompose
from schemas.artifacts import validate_artifact
ASIN="B08S3M2HKL"; MODEL="en_US-ljspeech-medium"
def text_from(path):
    _,sections=_parse_script(path)
    text=" ".join(s["text"] for s in sections)
    if not text.strip(): raise ValueError("No narration in script")
    return text
def build_edit_decisions(script,manifest,plan,stills):
    scenes={x["script_section_id"]:x for x in plan["scenes"]}
    cuts=[]; overlays=[]
    for i,s in enumerate(script["sections"]):
        start,end=float(s["start_seconds"]),float(s["end_seconds"])
        if end<=start: continue
        clip=None
        for ref in scenes.get(s["id"],{}).get("required_assets",[]):
            if ref.get("type")=="video":
                clip=next((x for x in manifest["assets"] if x["type"]=="video" and Path(x["path"]).name==ref["description"]),None)
                if clip: break
        span=end-start; clip_span=min(4,span*.42) if clip else 0
        if clip_span>=1:
            cuts.append({"id":s["id"]+"-video","source":clip["id"],"in_seconds":round(start,3),"out_seconds":round(start+clip_span,3),"layer":"primary","transition_in":"cut","transition_out":"cut"})
        still=stills[i%len(stills)]
        cuts.append({"id":s["id"]+"-still","source":still["id"],"in_seconds":round(start+clip_span,3),"out_seconds":round(end,3),"layer":"primary","transition_in":"cut","transition_out":"cut","transform":{"animation":"subtle-zoom-in"}})
        overlays.append({"type":"section_title","in_seconds":round(start,3),"out_seconds":round(min(end,start+2.4),3),"text":s["label"].replace("-"," ").title(),"position":"top-center"})
    return {"version":"1.0","tailPaddingSeconds":0,"cuts":cuts,"overlays":overlays,"renderer_family":"product-reveal","render_runtime":"remotion","composition_mode":"templated","metadata":{"project_id":"amazon-"+ASIN,"deliverable":"full_narrated_video","duration_seconds":script["total_duration_seconds"],"aspect_ratio":"16:9","fps":30,"audio":"narration","audio_expected":True,"voice_provider":"piper","voice_model":MODEL,"voice_fallback":False,"music_enabled":False,"external_assets_allowed":False,"generated_assets_allowed":False,"source_policy":"existing_supplied_media_only","notes":["Separate full-length edit; preview edit_decisions.json is not read or changed.","Existing product clips and stills only; product_image_61 excluded due conflicting 48-foot graphic."]}}


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


def main():
    p=argparse.ArgumentParser()
    for key in ("source-project","model","work-dir","output"): p.add_argument("--"+key,type=Path,required=True)
    p.add_argument("--approve-tts",action="store_true"); p.add_argument("--approve-render",action="store_true")
    a=p.parse_args()
    if not(a.approve_tts and a.approve_render): raise SystemExit("Blocked: both explicit approvals are required")
    if a.source_project.name!=ASIN: raise SystemExit("Only B08S3M2HKL supported")
    if not a.model.is_file() or not Path(str(a.model)+".json").is_file() or not shutil.which("piper"): raise RuntimeError("Pinned Piper runtime/model unavailable; no fallback")
    audio=a.source_project/"out"/"voice.wav"; audio.parent.mkdir(parents=True,exist_ok=True)
    proc=subprocess.run(["piper","--model",str(a.model),"--output_file",str(audio),"--length-scale","1.0","--sentence-silence","0.25"],input=text_from(a.source_project/"script.md"),text=True,encoding="utf-8",capture_output=True,timeout=900)
    if proc.returncode: raise RuntimeError("Piper failed: "+proc.stderr[-2000:])
    with wave.open(str(audio),"rb") as w:
        duration=w.getnframes()/w.getframerate()
        if duration<30: raise RuntimeError("Narration duration invalid")
    project=import_project(a.source_project,"amazon-"+ASIN+"-narrated",projects_dir=a.work_dir,media_mode="copy")
    root=project/"artifacts"; script=json.loads((root/"script.json").read_text()); manifest=json.loads((root/"asset_manifest.json").read_text()); plan=json.loads((root/"scene_plan.json").read_text())
    stills=[x for x in manifest["assets"] if x["type"]=="image" and "product_image_61" not in x["path"].lower()]
    if not stills: raise RuntimeError("No approved product stills")
    decisions=build_edit_decisions(script,manifest,plan,stills)
    validate_artifact("edit_decisions",decisions)
    render_manifest=manifest_with_absolute_paths(project,manifest)
    audio_for_render=project/"assets"/"audio"/"voice.wav"
    if not audio_for_render.is_file():
        raise RuntimeError("Imported narration WAV missing from isolated project workspace")
    (root/"narrated_edit_decisions.json").write_text(json.dumps(decisions,indent=2)+"\n")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    result=VideoCompose().execute({"operation":"render","edit_decisions":decisions,"asset_manifest":render_manifest,"audio_path":str(audio_for_render.resolve()),"output_path":str(a.output.resolve()),"profile":"youtube_landscape","script_text":text_from(a.source_project/"script.md"),"remotion_concurrency":2})
    if not result.success: raise RuntimeError("Remotion render failed: "+str(result.error))
    if not a.output.is_file() or a.output.stat().st_size<100000: raise RuntimeError("MP4 missing/invalid")
    final_review=(result.data or {}).get("final_review")
    if not isinstance(final_review,dict) or final_review.get("status")!="pass":
        raise RuntimeError("Final review did not pass: "+str(final_review))
    report={"provider":"Piper","model":MODEL,"female_voice":True,"fallback":False,"audio_duration_seconds":round(duration,2),"visual_cuts":len(decisions["cuts"]),"staged_assets":len(render_manifest["assets"]),"final_review":final_review}
    a.output.with_suffix(".report.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
if __name__=="__main__": main()
