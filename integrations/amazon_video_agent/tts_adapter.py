"""Ad Agent-compatible TTS providers for isolated OpenMontage renders.

Provider names, voice IDs, and model settings are read from the checked-out
Ad Agent config.yaml. Usage ledgers and generated audio stay in OpenMontage.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests
import yaml


def _split_text(text: str, limit: int) -> list[str]:
    """Split narration at sentence/word boundaries without dropping text."""
    text = re.sub(r"\\s+", " ", text).strip()
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = max(text.rfind(". ", 0, limit), text.rfind("? ", 0, limit),
                  text.rfind("! ", 0, limit), text.rfind("; ", 0, limit))
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return [x for x in chunks if x]


def _edge(text: str, voice: dict, out: Path) -> None:
    import edge_tts

    async def save():
        communicator = edge_tts.Communicate(
            text=text, voice=voice["voice"],
            rate=voice.get("rate", "-4%"),
            volume=voice.get("volume", "+0%"),
        )
        await communicator.save(str(out))

    asyncio.run(save())


def _eleven(text: str, voice: dict, settings: dict, out: Path) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Missing ELEVENLABS_API_KEY GitHub secret.")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice['voice_id']}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        params={"output_format": settings.get("output_format", "mp3_44100_128")},
        json={
            "text": text,
            "model_id": settings.get("model_id", settings.get("model", "eleven_multilingual_v2")),
            **({"voice_settings": settings["voice_settings"]} if settings.get("voice_settings") else {}),
        },
        timeout=180,
    )
    if response.status_code != 200 or not response.content:
        raise RuntimeError(f"ElevenLabs TTS error {response.status_code}: {response.text[:500]}")
    out.write_bytes(response.content)


def _openai(text: str, voice: dict, settings: dict, out: Path) -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Missing OPENAI_API_KEY GitHub secret.")
    response = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": settings.get("model", "gpt-4o-mini-tts"),
            "voice": voice["voice"],
            "input": text,
            "instructions": settings.get("instructions", ""),
            "response_format": "mp3",
        },
        timeout=180,
    )
    if response.status_code != 200 or not response.content:
        raise RuntimeError(f"OpenAI TTS error {response.status_code}: {response.text[:500]}")
    out.write_bytes(response.content)


def _inworld(text: str, voice: dict, settings: dict, out: Path, ledger_path: Path,
             approve_paid: bool) -> None:
    key = os.environ.get("INWORLD_API_KEY")
    if not key:
        raise RuntimeError("Missing INWORLD_API_KEY GitHub secret.")
    units = len(text.encode("utf-16-le")) // 2
    maximum = int(settings.get("max_utf16_units", 1900))
    if units > maximum:
        raise RuntimeError(f"Inworld segment exceeds {maximum} UTF-16 units ({units}).")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    used = int(ledger.get("inworld_characters_used", 0))
    budget = int(settings.get("free_character_budget", 40000))
    if used + units > budget and not (
        settings.get("allow_paid_fallback", False) and approve_paid
    ):
        raise RuntimeError(
            f"Inworld safety cap would be exceeded ({used}+{units}>{budget}). "
            "Paid overage requires config voice.inworld.allow_paid_fallback=true "
            "and per-run paid TTS approval."
        )
    response = requests.post(
        settings.get("endpoint", "https://api.inworld.ai/tts/v1/voice"),
        headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"},
        json={
            "text": text,
            "voiceId": voice.get("voice_id") or voice.get("voice"),
            "modelId": voice.get("model_id") or settings.get("model_id", "inworld-tts-2"),
            "audioConfig": {"audioEncoding": settings.get("audio_encoding", "MP3")},
        },
        timeout=180,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Inworld TTS error {response.status_code}: {response.text[:500]}")
    data = response.json()
    encoded = data.get("audioContent")
    if not encoded:
        raise RuntimeError("Inworld response did not contain audioContent.")
    out.write_bytes(base64.b64decode(encoded))
    actual = int(data.get("usage", {}).get("processedCharactersCount", units))
    ledger["inworld_characters_used"] = used + actual
    ledger["inworld_model_id"] = voice.get("model_id") or settings.get("model_id", "inworld-tts-2")
    ledger["inworld_voice_id"] = voice.get("voice_id") or voice.get("voice")
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\\n")


def _openrouter(text: str, voice: dict, settings: dict, out: Path, approve_paid: bool) -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Missing OPENROUTER_API_KEY GitHub secret.")
    if not settings.get("allow_paid", False):
        raise RuntimeError("OpenRouter is disabled in Ad Agent config.yaml (voice.openrouter.allow_paid=false).")
    if not settings.get("commercial_use_confirmed", False):
        raise RuntimeError("OpenRouter is blocked until commercial_use_confirmed=true for this exact model.")
    if not approve_paid:
        raise RuntimeError("OpenRouter requires the per-run paid TTS approval.")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if settings.get("http_referer"):
        headers["HTTP-Referer"] = settings["http_referer"]
    if settings.get("app_title"):
        headers["X-Title"] = settings["app_title"]
    response = requests.post(
        settings.get("endpoint", "https://openrouter.ai/api/v1/audio/speech"),
        headers=headers,
        json={
            "model": voice.get("model_id") or settings.get("model"),
            "input": text,
            "voice": voice.get("voice") or settings.get("voice"),
            "response_format": settings.get("response_format", "mp3"),
        },
        timeout=180,
    )
    if response.status_code != 200 or not response.content:
        raise RuntimeError(f"OpenRouter TTS error {response.status_code}: {response.text[:500]}")
    out.write_bytes(response.content)


def _validate_audio(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 44:
        raise RuntimeError(f"TTS provider returned an empty audio file: {path.name}")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"],
        check=True, capture_output=True, timeout=120,
    )


def synthesize_voice(text: str, voice_key: str, config_path: Path, output_wav: Path,
                     *, approve_paid: bool = False) -> dict:
    """Generate one consistent configured voice and normalize it to mono PCM WAV."""
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    voice_cfg = cfg.get("voice", {})
    options = voice_cfg.get("options", {})
    if voice_key not in options:
        raise ValueError(f"Unknown Ad Agent voice {voice_key!r}; configured options: {', '.join(options)}")
    voice = options[voice_key]
    provider = str(voice.get("provider", "")).lower()
    settings = voice_cfg.get(provider, {}) or {}
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    if provider not in {"edge", "elevenlabs", "openai", "inworld", "openrouter"}:
        raise ValueError(f"Unsupported configured TTS provider: {provider!r}")

    warnings = []
    if provider == "edge":
        warnings.append(
            "Uses the unofficial Microsoft Edge TTS endpoint; Ad Agent states there is no commercial licence statement."
        )
        max_chars = 3500
    elif provider == "elevenlabs":
        if not approve_paid:
            raise RuntimeError("ElevenLabs requires the per-run paid TTS approval.")
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY GitHub secret.")
        sub = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": key}, timeout=30,
        )
        if sub.status_code == 200 and sub.json().get("tier") == "free":
            raise RuntimeError("Ad Agent warns ElevenLabs free-plan output is non-commercial; use a paid plan for affiliate videos.")
        max_chars = 4500
    elif provider == "openai":
        if not approve_paid:
            raise RuntimeError("OpenAI TTS requires the per-run paid TTS approval.")
        max_chars = 3800
    elif provider == "openrouter":
        max_chars = 3000
        if not approve_paid:
            raise RuntimeError("OpenRouter requires the per-run paid TTS approval.")
    else:
        if not settings.get("enabled", True):
            raise RuntimeError("Inworld is disabled in Ad Agent config.yaml.")
        max_chars = int(settings.get("max_utf16_units", 1900))

    chunks = _split_text(text, max_chars)
    with tempfile.TemporaryDirectory(prefix="ad-agent-tts-", dir=output_wav.parent) as temp:
        temp_dir = Path(temp)
        rendered = []
        ledger = output_wav.parent / "inworld_usage.json"
        for index, chunk in enumerate(chunks, 1):
            audio = temp_dir / f"chunk-{index:04d}.mp3"
            if provider == "edge":
                _edge(chunk, voice, audio)
            elif provider == "elevenlabs":
                _eleven(chunk, voice, settings, audio)
            elif provider == "openai":
                _openai(chunk, voice, settings, audio)
            elif provider == "inworld":
                _inworld(chunk, voice, settings, audio, ledger, approve_paid)
            else:
                _openrouter(chunk, voice, settings, audio, approve_paid)
            _validate_audio(audio)
            rendered.append(audio)
        concat = temp_dir / "concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\\n" for p in rendered), encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", str(concat), "-ar", "22050", "-ac", "1",
             "-c:a", "pcm_s16le", str(output_wav)],
            check=True, capture_output=True, timeout=600,
        )
    _validate_audio(output_wav)
    gender = "female" if "female" in voice_key.lower() else "male" if "male" in voice_key.lower() else "unspecified"
    return {
        "provider": provider,
        "voice": voice.get("voice") or voice.get("voice_id") or voice_key,
        "gender": gender,
        "label": voice.get("label", voice_key),
        "warnings": warnings,
        "paid_tts_approved": bool(approve_paid),
        "chunks": len(chunks),
    }
