import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from utils import audioutils
from utils.config import (
    INVALID_AUDIO_FILE_MESSAGE,
    INVALID_AUDIO_TYPE_MESSAGE,
    get_message_limit,
)


class UploadedFile:
    def __init__(self, name, content_type, data):
        self.name = name
        self.type = content_type
        self._data = data

    def getbuffer(self):
        return memoryview(self._data)


def valid_wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(8000)
        audio_file.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


class AudioUtilityTests(unittest.TestCase):
    def test_upload_validation_accepts_wav_and_rejects_bad_content(self):
        valid_file = UploadedFile("mix.wav", "audio/wav", valid_wav_bytes())
        self.assertIsNone(audioutils.validate_audio_upload(valid_file))

        corrupt_file = UploadedFile("mix.wav", "audio/wav", b"not a wav file")
        self.assertEqual(
            audioutils.validate_audio_upload(corrupt_file),
            INVALID_AUDIO_FILE_MESSAGE,
        )

    def test_upload_validation_checks_type_and_size_before_decoding(self):
        unsupported = UploadedFile("mix.flac", "audio/flac", b"data")
        self.assertEqual(
            audioutils.validate_audio_upload(unsupported),
            INVALID_AUDIO_TYPE_MESSAGE,
        )

        oversized = UploadedFile("mix.wav", "audio/wav", b"12345")
        with patch.object(audioutils, "MAX_UPLOAD_SIZE_BYTES", 4):
            self.assertEqual(
                audioutils.validate_audio_upload(oversized),
                audioutils.UPLOAD_TOO_LARGE_MESSAGE,
            )

    def test_missing_audio_path_is_reported_as_validation_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_path = Path(temporary_directory) / "missing-track.wav"
            with self.assertRaisesRegex(ValueError, INVALID_AUDIO_FILE_MESSAGE):
                audioutils.analyze_audio_file(missing_path)

    def test_segments_are_clipped_and_invalid_ranges_are_rejected(self):
        audio = np.zeros(10_000, dtype=float)
        clipped = audioutils._extract_segments(
            audio,
            1000,
            (("Outro", 9.0, 12.0),),
        )
        self.assertEqual(clipped[0]["end"], 10.0)
        self.assertEqual(clipped[0]["audio"].shape[-1], 1000)

        with self.assertRaises(ValueError):
            audioutils._extract_segments(
                audio,
                1000,
                (("Invalid", -1.0, 1.0),),
            )

    def test_audio_analysis_returns_metrics_for_track_and_segment(self):
        sample_rate = 8000
        time_axis = np.arange(sample_rate * 4) / sample_rate
        mono = 0.1 * np.sin(2 * np.pi * 440 * time_axis)
        stereo = np.vstack((mono, mono))

        result = audioutils._analyze_audio(
            stereo,
            sample_rate,
            (("Verse", 0.0, 2.0),),
        )

        self.assertTrue(np.isfinite(result["loudness"]))
        self.assertEqual(len(result["spectrum"]), len(result["frequencies"]))
        self.assertEqual(result["segment_analyses"][0]["name"], "Verse")

    def test_free_message_limit_is_a_fixed_ten_messages(self):
        self.assertEqual(get_message_limit(), 10)
        self.assertEqual(get_message_limit(demo_package_active=True), 10)

    def test_too_long_audio_is_rejected_by_metadata(self):
        valid_file = UploadedFile("mix.wav", "audio/wav", valid_wav_bytes())
        info = type(
            "Info",
            (),
            {"duration": 20 * 60 + 1, "frames": 1000, "channels": 2},
        )()
        with patch.object(audioutils.sf, "info", return_value=info):
            with tempfile.TemporaryDirectory() as temporary_directory:
                temporary_path = Path(temporary_directory) / "mix.wav"
                temporary_path.write_bytes(valid_wav_bytes())
                self.assertEqual(
                    audioutils.validate_audio_upload(valid_file),
                    audioutils.MAX_AUDIO_DURATION_MESSAGE,
                )

    def test_mono_stereo_and_antiphase_metrics_do_not_cancel_loudness(self):
        sample_rate = 8000
        time_axis = np.arange(sample_rate * 2) / sample_rate
        mono = 0.2 * np.sin(2 * np.pi * 440 * time_axis)
        stereo = np.vstack((mono, mono))
        antiphase = np.vstack((mono, -mono))

        mono_result = audioutils._analyze_audio(mono, sample_rate)
        stereo_result = audioutils._analyze_audio(stereo, sample_rate)
        antiphase_result = audioutils._analyze_audio(antiphase, sample_rate)

        self.assertTrue(np.isfinite(stereo_result["loudness"]))
        self.assertTrue(np.isfinite(antiphase_result["loudness"]))
        self.assertLess(antiphase_result["phase_correlation"], -0.99)
        self.assertAlmostEqual(
            mono_result["max_peak_db"],
            stereo_result["max_peak_db"],
            delta=0.2,
        )


if __name__ == "__main__":
    unittest.main()
