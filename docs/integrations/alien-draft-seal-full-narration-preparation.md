# Alien Draft Seal — full narrated video preparation

**Project:** Amazon Video Agent ASIN `B08S3M2HKL`  
**Status:** preparation only; no audio generated and no full render authorized  
**Approved direction:** free, offline TTS; female US-English voice; use existing product footage and images only

## 1. Hard approval boundary

This preparation document does not authorize:
- generating even a short TTS sample;
- generating the complete narration WAV;
- rendering or publishing a full-length narrated video;
- adding music, sound effects, paid APIs, paid TTS, or externally generated media.

The existing 15-second silent preview workflow remains unchanged. Its audio-stripping step must never be reused for the narrated deliverable.

Each of the following requires a separate explicit approval from the user before execution:
1. a short voice sample;
2. the complete narration audio;
3. the full-length narrated video render.

## 2. Free female voice candidate and licensing notes

Candidate: **Piper `en_US-ljspeech-medium`**.

The upstream voice model card describes it as a single-speaker US-English female voice, medium quality, 22,050 Hz, trained from scratch on the LJ Speech dataset. The card identifies the dataset as public domain:
- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/MODEL_CARD
- Voice files: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium

The Piper project's commercial-use guide separately lists `en_US-ljspeech-medium` among voices whose training data chain permits commercial use:
- https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/COMMERCIAL_USE.md

Important distinctions:
- The voice model's dataset rights and the Piper engine's software licence are separate.
- The Piper engine is GPL-3.0-or-later; the project says it does not grant a separate licence over voice models.
- Preserve the exact model card with the downloaded model in the runner workspace and record the model revision/hash in the generation report.
- This is a technically promising candidate, not a legal opinion or a guarantee about every downstream use. Re-check the exact model card and upstream commercial-use guidance at execution time.

Piper's own commercial-use guidance:
https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md

## 3. Source narration and existing integration points

The approved source narration is:
`amazon-video-agent/projects/B08S3M2HKL/script.md`

The source project has seven scene groups and existing footage; no generated or external visuals are required.

The OpenMontage importer already checks for:
`projects/B08S3M2HKL/out/voice.wav`

If that WAV exists, the importer:
- reads its duration;
- imports it as the `amazon-narration` audio asset;
- records `narration_imported: true` in the asset manifest.

If the WAV does not exist, it estimates timing from script word counts. That estimate is not suitable as the final narration timing. A narrated render must use the actual WAV duration and verify the complete script against the generated audio.

## 4. Intended TTS execution design (not run)

Run TTS only on a GitHub-hosted Linux runner after the user approves audio generation.

1. Check out the two public repositories at pinned commit SHAs.
2. Install the pinned Piper runtime and FFmpeg on the hosted runner.
3. Download the exact `en_US-ljspeech-medium` ONNX model and its adjacent JSON config; retain the model card and record hashes.
4. Read the approved `script.md` narration lines in source order. Exclude headings, tags, comments, and editing metadata.
5. Generate narration with one fixed Piper model and one voice only.
6. Fail closed on any missing executable, model, model-card, invalid WAV, or synthesis error. No Edge, Inworld, cloud, paid, or gender-changing fallback.
7. Assemble the per-section WAV outputs in source order, preserving section boundaries and deliberate pauses.
8. Save the resulting WAV to the source project's `out/voice.wav` only inside the ephemeral runner workspace. Do not commit generated audio or model weights to either repository.
9. Run audio integrity checks: WAV decodes, non-zero duration, expected sample rate, no empty sections, and measured duration report.
10. Upload a review-only audio artifact only after the relevant audio-generation approval.

No step above has been executed.

## 5. Full-length video path

The existing silent-preview workflow has a deliberate `-an` audio-strip step and must remain untouched.

A separate narrated workflow is required. It must:
- be manually dispatched and project-specific;
- validate the source project and approvals before doing work;
- require an explicit narration-generation gate and a separate full-render gate, both defaulting to false;
- refuse to use the 15-second preview edit decisions as a full-length timeline;
- use the actual generated WAV duration and section timings;
- use only the existing approved video/image assets;
- attach narration only, with no music or SFX unless separately approved;
- never strip audio from the narrated output;
- verify H.264 video, an AAC audio stream, expected duration, full-file decode, and narration/video synchronization;
- produce a final review report that checks audio presence and script coverage.

The Remotion `Explainer` composition already has a narration audio layer (`audio.narration.src`). The native render path also supports an external audio-mux input. Before implementing the new workflow, the exact edit-decision-to-Remotion prop mapping and the final-review transcript path must be tested end-to-end. Do not assume that a schema field alone proves the audio is wired to the rendered composition.

## 6. Current known constraints and unresolved implementation checks

- The full script is substantially longer than the approved 15-second preview. The preview's five 3-second cuts are not a valid full-length edit.
- Exact narration duration is unknown until audio generation is explicitly approved.
- The current importer anchors scene groups to script sections and limits each imported scene beat to about five seconds. A separate full-length edit plan must deliberately reuse and sequence the supplied footage across the complete narration without implying unsupported product performance.
- The existing scene plan is an import seed, not a finished full-length edit.
- The full narration must retain the script's qualifications: fit and surface compatibility matter; the footage illustrates application only; no personal testing, guaranteed draft reduction, energy savings, or temperature improvement claims.

## 7. Readiness checklist

- [x] Free TTS direction selected.
- [x] Female voice preference selected.
- [x] Candidate voice identified and commercial-use guidance located.
- [x] Existing importer hook for `out/voice.wav` confirmed.
- [x] Remotion narration layer and native audio-mux capability confirmed.
- [ ] Pin and validate Piper runtime/version on GitHub Actions.
- [ ] Implement a tested, fail-closed narration generator.
- [ ] Confirm edit-decision audio asset mapping through the Remotion renderer.
- [ ] Build a separate full-length timeline and narrated workflow.
- [ ] Obtain approval for a short voice sample before generating audio.
- [ ] Obtain approval for complete narration before generating the full WAV.
- [ ] Obtain approval for full-length render before rendering the video.

**No audio generation or full-length render is permitted by this preparation plan.**
