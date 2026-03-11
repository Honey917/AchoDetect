#!/usr/bin/env python3
"""Forensic-style audio feature extraction and scoring for AchoDetect."""

from __future__ import annotations

import hashlib
import math
import shutil
import struct
import subprocess
import tempfile
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ForensicVoiceModel:
    """A transparent forensic-style model using interpretable waveform features.

    This is still a lightweight approximation, but unlike simple RMS heuristics,
    it uses multiple complementary signals:
      - duration
      - RMS loudness
      - zero-crossing rate
      - amplitude dynamics (frame energy variance)
      - clipping ratio
    """

    engine_name = "AchoDetect-Forensic-v2"

    def analyze(self, file_path: Path) -> dict[str, Any]:
        size_bytes = file_path.stat().st_size
        extension = file_path.suffix.lower().lstrip(".") or "unknown"

        payload: dict[str, Any] = {
            "filename": file_path.name,
            "extension": extension,
            "size_bytes": size_bytes,
            "sha256": self._sha256(file_path),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "engine": self.engine_name,
        }

        # Direct WAV path
        if extension == "wav":
            features = self._extract_wav_features(file_path)
            scores = self._score(features)
            payload["features"] = features
            payload["result"] = scores["result"]
            payload["confidence_band"] = scores["confidence_band"]
            payload["forensic_rationale"] = scores["rationale"]
            payload["notice"] = (
                "This forensic model is a transparent software signal-analysis layer and "
                "should be used as investigative guidance with expert human review."
            )
            return payload

        # Non-WAV path: try ffmpeg conversion (for phone recordings/calls like m4a/mp3).
        converted_wav = self._convert_to_wav(file_path)
        if converted_wav:
            features = self._extract_wav_features(converted_wav)
            scores = self._score(features)
            payload["features"] = features
            payload["result"] = scores["result"]
            payload["confidence_band"] = scores["confidence_band"]
            payload["forensic_rationale"] = scores["rationale"]
            payload["notice"] = (
                "Input was converted to WAV for forensic analysis (suitable for phone recordings/call exports). "
                "Use original high-quality source when possible."
            )
            payload["converted_from"] = extension
            return payload

        # Fallback when conversion tools are unavailable.
        payload["features"] = {
            "duration_seconds": None,
            "sample_rate_hz": None,
            "channels": None,
            "rms_amplitude": None,
            "zero_crossing_rate": None,
            "energy_variability": None,
            "clipping_ratio": None,
        }
        payload["result"] = {
            "label": "Unknown (non-WAV)",
            "authenticity_score": 0.5,
            "synthetic_score": 0.5,
        }
        payload["notice"] = (
            "Forensic feature extraction supports PCM WAV directly. "
            "For MP3/M4A/phone-call recordings, install ffmpeg on the server or upload WAV."
        )
        payload["confidence_band"] = "low"
        return payload

    @staticmethod
    def _sha256(file_path: Path) -> str:
        return hashlib.sha256(file_path.read_bytes()).hexdigest()

    @staticmethod
    def _convert_to_wav(file_path: Path) -> Path | None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None

        tmp_dir = Path(tempfile.gettempdir())
        out_path = tmp_dir / f"achodetect_{file_path.stem}_{uuid.uuid4().hex[:8]}.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(file_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(out_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0 or not out_path.exists():
            return None
        return out_path

    def _extract_wav_features(self, file_path: Path) -> dict[str, Any]:
        with wave.open(str(file_path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.getnframes()
            duration_seconds = frames / float(sample_rate) if sample_rate else 0.0
            raw = wav_file.readframes(frames)

        if sample_width != 2 or not raw:
            return {
                "duration_seconds": round(duration_seconds, 3),
                "sample_rate_hz": sample_rate,
                "channels": channels,
                "rms_amplitude": 0.0,
                "zero_crossing_rate": 0.0,
                "energy_variability": 0.0,
                "clipping_ratio": 0.0,
            }

        total_samples = len(raw) // 2
        unpacked = struct.unpack(f"<{total_samples}h", raw)

        if channels > 1:
            samples = unpacked[::channels]
        else:
            samples = unpacked

        n = len(samples)
        if n == 0:
            return {
                "duration_seconds": round(duration_seconds, 3),
                "sample_rate_hz": sample_rate,
                "channels": channels,
                "rms_amplitude": 0.0,
                "zero_crossing_rate": 0.0,
                "energy_variability": 0.0,
                "clipping_ratio": 0.0,
            }

        rms = math.sqrt(sum(s * s for s in samples) / n)

        zero_crosses = 0
        for i in range(1, n):
            if (samples[i - 1] < 0 <= samples[i]) or (samples[i - 1] >= 0 > samples[i]):
                zero_crosses += 1
        zcr = zero_crosses / n

        clipping_samples = sum(1 for s in samples if abs(s) >= 32700)
        clipping_ratio = clipping_samples / n

        frame_size = max(256, int(sample_rate * 0.02))
        energies: list[float] = []
        for i in range(0, n, frame_size):
            frame = samples[i : i + frame_size]
            if not frame:
                continue
            eng = sum(abs(s) for s in frame) / len(frame)
            energies.append(eng)

        if energies:
            mean_eng = sum(energies) / len(energies)
            variance = sum((e - mean_eng) ** 2 for e in energies) / len(energies)
            energy_variability = math.sqrt(variance) / (mean_eng + 1e-6)
        else:
            energy_variability = 0.0

        return {
            "duration_seconds": round(duration_seconds, 3),
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "rms_amplitude": round(rms, 3),
            "zero_crossing_rate": round(zcr, 6),
            "energy_variability": round(energy_variability, 6),
            "clipping_ratio": round(clipping_ratio, 6),
        }

    def _score(self, features: dict[str, Any]) -> dict[str, Any]:
        duration = float(features["duration_seconds"] or 0.0)
        rms = float(features["rms_amplitude"] or 0.0)
        zcr = float(features["zero_crossing_rate"] or 0.0)
        energy_var = float(features["energy_variability"] or 0.0)
        clipping = float(features["clipping_ratio"] or 0.0)

        duration_score = min(duration / 10.0, 1.0)
        rms_score = min(rms / 9000.0, 1.0)
        zcr_center = 0.06
        zcr_score = max(0.0, 1.0 - min(abs(zcr - zcr_center) / 0.06, 1.0))
        dynamics_score = max(0.0, 1.0 - min(abs(energy_var - 0.28) / 0.28, 1.0))
        clipping_penalty = min(clipping / 0.015, 1.0)

        authenticity = (
            (0.22 * duration_score)
            + (0.22 * rms_score)
            + (0.24 * zcr_score)
            + (0.24 * dynamics_score)
            + (0.08 * (1.0 - clipping_penalty))
        )
        authenticity = round(max(0.0, min(authenticity, 0.99)), 3)
        synthetic = round(1.0 - authenticity, 3)

        if authenticity >= 0.62:
            label = "Likely Human"
            band = "high"
        elif authenticity >= 0.48:
            label = "Inconclusive"
            band = "medium"
        else:
            label = "Likely Synthetic"
            band = "high"

        rationale = [
            f"Duration contribution: {duration_score:.3f}",
            f"Loudness contribution: {rms_score:.3f}",
            f"Zero-crossing naturalness: {zcr_score:.3f}",
            f"Temporal energy dynamics: {dynamics_score:.3f}",
            f"Clipping penalty: {clipping_penalty:.3f}",
        ]

        return {
            "result": {
                "label": label,
                "authenticity_score": authenticity,
                "synthetic_score": synthetic,
            },
            "confidence_band": band,
            "rationale": rationale,
        }
