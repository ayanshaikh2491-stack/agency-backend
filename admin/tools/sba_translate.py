"""SBA Translation Engine — Live meeting translation + transcription + summary.

No extra API cost. Uses SBA's existing LLM for everything.

Translation Flow:
  Client (English/other) → translate_for_owner() → Hinglish (aapko dikhe)
  Aap (Hinglish) → translate_for_client() → English/other (client ko jaye)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import openai
from admin.config import settings

logger = logging.getLogger(__name__)

# Default meeting notes directory (repo-root/data/meetings)
DEFAULT_NOTES_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "meetings")


def _tts_provider() -> str:
    return os.getenv("SBA_TTS_PROVIDER", "mock").lower()


def _mock_wav_bytes(seconds: float = 1.0) -> bytes:
    """Deterministic tiny WAV (8kHz 16-bit mono silence) for mock TTS."""
    rate = 8000
    frames = int(rate * seconds)
    data = b"\x00\x00" * frames
    header = b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt "
    header += (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    header += rate.to_bytes(4, "little") + (rate * 2).to_bytes(4, "little")
    header += (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + b"data"
    header += len(data).to_bytes(4, "little")
    return header + data


class SBATranslationEngine:
    """Translate meeting conversations in real-time + transcribe + summarize."""

    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=settings.WORKSPACE_API_KEY or None,
            base_url=settings.WORKSPACE_API_BASE or None,
        )
        self._model = settings.WORKSPACE_AGENT_MODEL

    async def translate_for_owner(
        self,
        text: str,
        source_lang: str = "English",
    ) -> str:
        """Translate client's message to Hinglish for the owner.

        Client English bole → Aapko Hinglish mein dikhe.
        """
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a real-time meeting translator. "
                        f"Translate the following {source_lang} text to Hinglish "
                        "(Hindi + Urdu + English mix, natural conversational tone). "
                        "Output ONLY the translation, no explanations or quotes."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        return resp.choices[0].message.content or text

    async def translate_for_client(
        self,
        text: str,
        target_lang: str = "English",
    ) -> str:
        """Translate owner's Hinglish to professional English for the client."""
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Translate this Hinglish message to professional {target_lang}. "
                        "Keep it polite and business-appropriate. "
                        "Output ONLY the translation."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        return resp.choices[0].message.content or text

    async def transcribe_audio(self, audio_path: str) -> list[dict[str, Any]]:
        """Transcribe an audio file to text segments using LLM vision.

        Args:
            audio_path: Path to audio file.

        Returns: List of {speaker, text, timestamp} segments.
        """
        import base64
        try:
            with open(audio_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode()

            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Transcribe this meeting audio to text. "
                            "Identify different speakers if possible. "
                            "Output JSON array: "
                            '[{"speaker": "Client/Owner/Unknown", '
                            '"text": "...", '
                            '"timestamp": "MM:SS"}]'
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "audio_url",
                                "audio_url": {"url": f"data:audio/wav;base64,{audio_b64}"},
                            }
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            text = resp.choices[0].message.content or "[]"
            # Clean markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0]
            return json.loads(text.strip())
        except Exception as exc:
            logger.exception("Transcription failed: %s", exc)
            return [{"speaker": "Unknown", "text": "[Transcription failed]", "timestamp": "00:00"}]

    async def generate_summary(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate structured meeting summary from transcript segments.

        Returns:
          - key_points: list of key discussion points
          - action_items: list of action items with owner
          - decisions: list of decisions made
          - next_steps: list of next steps
          - full_summary: narrative summary in Hinglish
        """
        transcript_text = "\n".join(
            f"[{s.get('timestamp', '')}] {s.get('speaker', '?')}: {s.get('text', '')}"
            for s in segments
        )

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are SBA's meeting analyst. Summarize this meeting transcript. "
                        "Respond in JSON only with keys: "
                        "key_points (list), action_items (list of {task, owner, deadline}), "
                        "decisions (list), next_steps (list), "
                        "full_summary (str — detailed Hinglish summary for the owner)"
                    ),
                },
                {
                    "role": "user",
                    "content": transcript_text[:8000],
                },
            ],
            temperature=0.1,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        return json.loads(text)

    async def translate_meeting_live(
        self,
        segments: list[dict[str, Any]],
        owner_name: str = "Aap",
    ) -> list[dict[str, Any]]:
        """Translate an entire meeting transcript in one pass.

        Each segment gets translated based on speaker:
          - Client → Hinglish (owner ke liye)
          - Owner → English (client ke liye)

        Returns: Segments with added 'translation' key.
        """
        result = []
        for seg in segments:
            speaker = seg.get("speaker", "").lower()
            text = seg.get("text", "")

            if "client" in speaker or "lead" in speaker:
                translation = await self.translate_for_owner(text)
            else:
                translation = await self.translate_for_client(text)

            seg["translation"] = translation
            result.append(seg)

        return result

    async def synthesize(self, text: str, lang: str = "en") -> dict[str, Any]:
        """Speech-to-speech: turn translated text into audio.

        Providers (env SBA_TTS_PROVIDER):
          - belt:      inference.sh CLI (`belt app run inworld/text-to-speech-2`)
          - openai:    OpenAI-compatible /v1/audio/speech (uses WORKSPACE keys)
          - mock:      deterministic silent WAV (default; safe offline)

        Returns: {"audio_b64": str, "provider": str, "lang": str}
        """
        provider = _tts_provider()
        audio: bytes | None = None

        if provider == "belt":
            audio = self._belt_synthesize(text, lang)
        elif provider == "openai":
            try:
                resp = await self._client.audio.speech.create(
                    model=os.getenv("SBA_TTS_MODEL", "tts-1"),
                    voice=os.getenv("SBA_TTS_VOICE", "alloy"),
                    input=text,
                )
                audio = resp.content
            except Exception as exc:
                logger.exception("OpenAI TTS failed: %s", exc)
        else:
            logger.warning(
                "TTS provider '%s' not configured — returning mock audio. "
                "Set SBA_TTS_PROVIDER=belt|openai for real speech.",
                provider,
            )

        if not audio:
            audio = _mock_wav_bytes()
            provider = "mock"
        return {
            "audio_b64": base64.b64encode(audio).decode(),
            "provider": provider,
            "lang": lang,
        }

    def _belt_synthesize(self, text: str, lang: str) -> bytes | None:
        """Shell out to inference.sh CLI for real TTS. Returns audio bytes or None."""
        inp = {"text": text, "voice_id": os.getenv("SBA_TTS_VOICE", "Sarah")}
        if lang and lang.lower() != "en":
            inp["lang"] = lang
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(inp, f)
            out = subprocess.run(
                ["belt", "app", "run", "inworld/text-to-speech-2", "--input", path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if out.returncode != 0:
                logger.warning("belt TTS stderr: %s", out.stderr[-500:])
                return None
            data = json.loads(out.stdout or "{}")
            url = (
                data.get("url")
                or data.get("audio_url")
                or (data.get("output") or {}).get("url")
            )
            if not url:
                logger.warning("belt TTS response had no audio URL: %s", str(data)[:300])
                return None
            return urlopen(url, timeout=60).read()
        except Exception as exc:
            logger.exception("belt TTS failed: %s", exc)
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def process_meeting(
        self,
        audio_path: str,
        meeting_id: str | None = None,
        notes_dir: str | None = None,
    ) -> dict[str, Any]:
        """Full meeting flow: transcribe → translate → summarize → save notes.

        Saves notes to a local markdown file (data/meetings/<meeting_id|ts>.md)
        and — when meeting_id is given — appends each translated segment plus a
        summary note to the meeting record via sba_store.add_meeting_note.

        Returns:
          {
            "segments": [transcribed+translated segments],
            "summary": {...},
            "notes_file": str | None,
            "saved_to_meeting": bool,
          }
        """
        segments = await self.transcribe_audio(audio_path)
        translated = await self.translate_meeting_live(segments)
        summary = await self.generate_summary(translated)

        notes_dir = notes_dir or os.getenv("SBA_MEETING_NOTES_DIR", DEFAULT_NOTES_DIR)
        os.makedirs(notes_dir, exist_ok=True)
        notes_file = None
        saved_to_meeting = False

        if meeting_id:
            from admin.agency import sba_store

            for seg in translated:
                await sba_store.add_meeting_note(
                    mid=meeting_id,
                    text=f"{seg.get('text', '')}  →  {seg.get('translation', '')}",
                    language=seg.get("language", "en"),
                    speaker=seg.get("speaker", "Unknown"),
                )
            await sba_store.add_meeting_note(
                mid=meeting_id,
                text=json.dumps(summary, ensure_ascii=False)[:2000],
                language="en",
                speaker="system",
            )
            saved_to_meeting = True
            notes_file = str(Path(notes_dir) / f"{meeting_id}.md")
        else:
            import time

            notes_file = str(Path(notes_dir) / f"meeting_{int(time.time())}.md")

        self._write_notes_file(notes_file, translated, summary)

        return {
            "segments": translated,
            "summary": summary,
            "notes_file": notes_file,
            "saved_to_meeting": saved_to_meeting,
        }

    def _write_notes_file(
        self,
        path: str,
        segments: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        lines = ["# SBA Meeting Notes", ""]
        for seg in segments:
            ts = seg.get("timestamp", "")
            speaker = seg.get("speaker", "?")
            text = seg.get("text", "")
            translation = seg.get("translation", "")
            lines.append(f"## [{ts}] {speaker}")
            lines.append(f"- Original: {text}")
            if translation:
                lines.append(f"- Translation: {translation}")
            lines.append("")
        lines.append("## Summary")
        lines.append(summary.get("full_summary", ""))
        for key in ("key_points", "action_items", "decisions", "next_steps"):
            val = summary.get(key)
            if val:
                lines.append(f"\n### {key.replace('_', ' ').title()}")
                if isinstance(val, list):
                    for item in val:
                        lines.append(f"- {item}")
                else:
                    lines.append(f"- {val}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(lines), encoding="utf-8")
