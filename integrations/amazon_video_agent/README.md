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

## GitHub Actions: any ASIN / project

The **Amazon Video Agent Integration Test** workflow accepts a project folder
name through the `project_id` input. Use the folder name under
`Swar-K/amazon-video-agent/projects/` (for example, `B08S3M2HKL`).
The import, artifact checks, review ZIP, and short FFmpeg smoke render run for
the selected project. These checks do not require an OpenMontage approval
package and do not create edit decisions.

Native Remotion rendering is deliberately separate and opt-in:

1. Complete the selected project's idea, script, scene-plan, assets, and edit
   checkpoints in OpenMontage.
2. Commit that project's approved files in this repository under
   `projects/amazon-<project_id>/`, including the five checkpoint JSON files,
   `decision_log.json`, and the approved `artifacts/scene_plan.json`,
   `artifacts/asset_manifest.json`, `artifacts/edit_decisions.json`, and
   `artifacts/proposal_packet.json`.
3. In GitHub Actions, run **Amazon Video Agent Integration Test** on
   `main`, enter the project folder name, and set
   `render_native_preview` to **true**.

The native-render job validates the selected project's own checkpoints,
approved asset mappings, cut timing, render duration, and Remotion settings.
It does not assume a fixed ASIN, product image filename, five-scene edit, or
15-second duration. If the approval package is missing or incomplete, the job
fails with a list of the exact missing files; it never borrows approvals from
another ASIN.

Leave `render_native_preview` false for new projects until their own
downstream approvals and edit decisions are complete. Pull-request validation
runs import and smoke tests only; it no longer tries to render the original
sample as a side effect.
