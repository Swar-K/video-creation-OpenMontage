# Amazon Video Agent integration

This adapter imports an existing Amazon Video Agent product project into an
OpenMontage hybrid workspace. It does not replace either project's normal
workflow and does not render video itself.

## Imported data

- facts.yaml -> artifacts/brief.json
- script.md -> timestamped artifacts/script.json
- scenes.md -> artifacts/scene_plan.json
- assets/* and optional out/voice.wav -> artifacts/asset_manifest.json

The source project is not edited. Media is symlinked by default to avoid
duplicating large files. Use --media-mode copy if the checkouts are on
different machines or symlinks are unavailable.

## Run

From the OpenMontage repository root, with its Python requirements installed:

    python scripts/import_amazon_video_agent.py \
      --amazon-project ../amazon-video-agent/projects/B00002NCJI \
      --project-id amazon-B00002NCJI

For independent copies:

    python scripts/import_amazon_video_agent.py \
      --amazon-project ../amazon-video-agent/projects/B00002NCJI \
      --project-id amazon-B00002NCJI-copy \
      --media-mode copy

## Behavior and limitations

- Existing Amazon narration is reused if out/voice.wav exists. The importer
  never invokes TTS or a paid API.
- Section timestamps are estimated by narration word count. If voice.wav is
  present, its duration anchors the overall timeline. Review timings before
  editing.
- Amazon scene prompts are translated into native scene-plan entries.
- The importer does not invent edit_decisions or bypass human approval gates.
- OpenMontage's youtube_shorts profile is limited to 60 seconds. For longer
  vertical derivatives, use a suitable supported profile or an explicit
  1080x1920 compose target after confirming platform constraints.
- Landscape and vertical deliverables require separately reviewed framing;
  do not assume a centre crop will preserve product labels and important detail.
