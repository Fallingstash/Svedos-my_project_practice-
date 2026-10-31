from pathlib import Path
from tempfile import TemporaryDirectory

import librosa
import numpy as np
import pyloudnorm as pyln
import streamlit as st
from scipy.signal import find_peaks, resample_poly

from utils.config import (
    INVALID_AUDIO_FILE_MESSAGE,
    INVALID_AUDIO_TYPE_MESSAGE,
    MAX_UPLOAD_SIZE_BYTES,
    SUPPORTED_AUDIO_EXTENSIONS,
    UPLOAD_TOO_LARGE_MESSAGE,
)

FFT_SIZE = 2048
TRUE_PEAK_OVERSAMPLE = 4
LUFS_WINDOW_SECONDS = 3
FREQUENCY_BANDS = (
    (20, 80, "20-80 Гц"),
    (80, 250, "80-250 Гц"),
    (250, 500, "250-500 Гц"),
    (500, 2000, "500-2 000 Гц"),
    (2000, 5000, "2-5 кГц"),
    (5000, 10000, "5-10 кГц"),
    (10000, 16000, "10-16 кГц"),
)


def validate_audio_upload(uploaded_file):
    if uploaded_file is None:
        return None

    content_type = str(getattr(uploaded_file, "type", "") or "").lower()
    if not content_type.startswith("audio/"):
        return INVALID_AUDIO_TYPE_MESSAGE

    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        return INVALID_AUDIO_TYPE_MESSAGE

    file_buffer = uploaded_file.getbuffer()
    if file_buffer.nbytes > MAX_UPLOAD_SIZE_BYTES:
        return UPLOAD_TOO_LARGE_MESSAGE

    try:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory) / f"upload{extension}"
            temporary_path.write_bytes(file_buffer)
            audio, _ = librosa.load(
                str(temporary_path),
                sr=None,
                mono=False,
                duration=1,
            )
            if audio.size == 0:
                return INVALID_AUDIO_FILE_MESSAGE
    except Exception:
        return INVALID_AUDIO_FILE_MESSAGE
    return None


def _format_energy(spectrum, frequencies, lower, upper):
    mask = (frequencies >= lower) & (frequencies < upper)
    if not np.any(mask):
        return "нет данных"
    return f"{np.mean(spectrum[mask]):.1f} дБ"


def get_genre_reference_curve(genre, frequencies):
    normalized_genre = str(genre).casefold().replace("-", "").replace(" ", "")
    if normalized_genre in {"рэп", "хипхоп", "hiphop"}:
        anchors = (
            (20, -3),
            (40, 1),
            (60, 3),
            (80, 2),
            (150, 0),
            (250, -2),
            (350, -1),
            (500, 0),
            (1000, 0),
            (4000, 0),
            (8000, 0),
            (10000, -0.5),
            (16000, -2),
        )
    elif normalized_genre == "поп":
        anchors = (
            (20, -2),
            (40, -1),
            (80, 0),
            (200, 0),
            (500, 0),
            (1000, 0),
            (2000, 1.5),
            (3000, 2),
            (4000, 1.5),
            (6000, 0),
            (10000, -0.5),
            (16000, -1),
        )
    elif normalized_genre in {"лоуфай", "lofi"}:
        anchors = (
            (20, -1),
            (40, 1),
            (80, 2),
            (160, 1),
            (300, 0),
            (1000, 0),
            (3000, -1),
            (6000, -2),
            (8000, -3),
            (10000, -7),
            (16000, -14),
        )
    else:
        anchors = (
            (20, 0),
            (20000, 0),
        )

    anchor_frequencies = np.asarray([point[0] for point in anchors], dtype=float)
    anchor_levels = np.asarray([point[1] for point in anchors], dtype=float)
    safe_frequencies = np.maximum(np.asarray(frequencies, dtype=float), 1.0)
    curve = np.interp(
        np.log10(safe_frequencies),
        np.log10(anchor_frequencies),
        anchor_levels,
    )
    return curve - np.max(curve)


def get_genre_reference_delta(audio_data, genre):
    user_spectrum = np.asarray(audio_data["spectrum"], dtype=float)
    frequencies = np.asarray(audio_data["frequencies"], dtype=float)
    reference_curve = get_genre_reference_curve(genre, frequencies)
    band_deltas = []
    for lower, upper, label in FREQUENCY_BANDS:
        mask = (frequencies >= lower) & (frequencies < upper)
        if not np.any(mask):
            continue
        delta_db = float(np.mean(user_spectrum[mask] - reference_curve[mask]))
        if abs(delta_db) > 3:
            direction = "выше" if delta_db > 0 else "ниже"
            band_deltas.append(
                f"{label}: энергия {direction} жанрового ориентира "
                f"примерно на {abs(delta_db):.1f} дБ"
            )

    if not band_deltas:
        return (
            f"Для жанра «{genre}» ни в одной анализируемой полосе "
            "отклонение от жанрового ориентира не превышает 3 дБ."
        )
    return (
        f"Сравнение с ориентировочной кривой жанра «{genre}» "
        "(разница указана для внутренних рекомендаций):\n"
        + "\n".join(f"- {delta}" for delta in band_deltas)
    )


def _extract_segments(audio, sample_rate, segments):
    duration = audio.shape[-1] / sample_rate
    extracted_segments = []
    for name, start, end in segments:
        if start < 0 or end <= start or start >= duration:
            raise ValueError(
                f"Исправь таймкоды сегмента «{name}»: начало должно быть "
                f"раньше длительности трека ({duration:.1f} сек.), а конец — "
                "позже начала."
            )
        clipped_end = min(end, duration)
        start_sample = int(start * sample_rate)
        end_sample = min(int(clipped_end * sample_rate), audio.shape[-1])
        segment_audio = audio[..., start_sample:end_sample]
        if segment_audio.shape[-1] <= 0:
            raise ValueError(
                f"Сегмент «{name}» слишком короткий после ограничения "
                f"длительностью трека ({duration:.1f} сек.)."
            )
        if segment_audio.shape[-1] < int(sample_rate * 0.4):
            raise ValueError(
                f"Сегмент «{name}» слишком короткий для анализа "
                "(менее 0,4 сек.)."
            )
        extracted_segments.append(
            {
                "name": name,
                "start": float(start),
                "end": float(clipped_end),
                "audio": segment_audio,
            }
        )

    return extracted_segments


def _prepare_audio(audio, sample_rate, segments):
    extracted_segments = _extract_segments(audio, sample_rate, segments)
    if extracted_segments:
        labels = [
            f"«{segment['name']}» "
            f"({segment['start']:g}-{segment['end']:g} сек.)"
            for segment in extracted_segments
        ]
        audio = np.concatenate(
            [segment["audio"] for segment in extracted_segments],
            axis=-1,
        )
        context = "Анализ выбранных фрагментов: " + ", ".join(labels) + "."
    else:
        context = "Анализ всего трека."

    if audio.shape[-1] < int(sample_rate * 0.4):
        raise ValueError(
            "Для анализа выбран слишком короткий фрагмент (менее 0,4 сек.)."
        )
    return audio, context


def _stereo_metrics(audio):
    if audio.ndim < 2 or audio.shape[0] < 2:
        return 0.0, float("-inf")

    left = audio[0]
    right = audio[1]
    mid = (left + right) * 0.5
    side = (left - right) * 0.5
    mid_rms = float(np.sqrt(np.mean(mid**2)))
    side_rms = float(np.sqrt(np.mean(side**2)))
    width_ratio = (
        side_rms / mid_rms
        if mid_rms > 0
        else (float("inf") if side_rms > 0 else 0.0)
    )
    width_db = 20 * np.log10(width_ratio) if width_ratio > 0 else float("-inf")
    return width_ratio * 100, width_db


def _phase_correlation(audio):
    if audio.ndim < 2 or audio.shape[0] < 2:
        return 1.0

    left = np.asarray(audio[0], dtype=float)
    right = np.asarray(audio[1], dtype=float)
    if left.size == 0 or right.size == 0:
        return 0.0

    left_centered = left - np.mean(left)
    right_centered = right - np.mean(right)
    denominator = np.linalg.norm(left_centered) * np.linalg.norm(right_centered)
    if denominator == 0:
        return 1.0 if np.array_equal(left, right) else 0.0
    correlation = float(np.dot(left_centered, right_centered) / denominator)
    return float(np.clip(correlation, -1.0, 1.0))


def _short_term_lufs(mono_audio, sample_rate):
    window_size = LUFS_WINDOW_SECONDS * sample_rate
    if len(mono_audio) < window_size * 2:
        return None

    meter = pyln.Meter(sample_rate)
    measurements = [
        meter.integrated_loudness(mono_audio[start : start + window_size])
        for start in range(0, len(mono_audio) - window_size + 1, window_size)
    ]
    finite_measurements = np.asarray(measurements, dtype=float)
    finite_measurements = finite_measurements[np.isfinite(finite_measurements)]
    if len(finite_measurements) < 2:
        return None
    return float(np.var(finite_measurements))


def _analyze_audio(audio, sample_rate, segments=()):
    source_audio = audio
    selected_audio, analysis_context = _prepare_audio(audio, sample_rate, segments)
    stereo_width_pct, stereo_width_db = _stereo_metrics(selected_audio)
    mono_audio = librosa.to_mono(selected_audio) if selected_audio.ndim == 2 else selected_audio
    phase_correlation = _phase_correlation(selected_audio)

    stft = librosa.stft(mono_audio, n_fft=FFT_SIZE)
    magnitude = np.abs(stft)
    average_magnitude = np.mean(magnitude, axis=1)
    absolute_spectrum = librosa.amplitude_to_db(
        average_magnitude,
        ref=1.0,
        top_db=None,
    )
    normalized_spectrum = librosa.amplitude_to_db(
        average_magnitude,
        ref=max(float(np.max(average_magnitude)), 1e-10),
    )
    spectralcentroid = float(
        np.mean(
            librosa.feature.spectral_centroid(
                y=mono_audio,
                sr=sample_rate,
                n_fft=FFT_SIZE,
            )
        )
    )
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=FFT_SIZE)

    peaks, _ = find_peaks(normalized_spectrum, prominence=5)
    peak_amplitudes = normalized_spectrum[peaks]
    sorted_indices = np.argsort(peak_amplitudes)[::-1]

    top_peaks = []
    request_lines = []
    for rank, sorted_index in enumerate(sorted_indices[:3], start=1):
        peak_index = peaks[sorted_index]
        frequency = frequencies[peak_index]
        db = peak_amplitudes[sorted_index]
        request_lines.append(
            f"{rank}. {frequency:.1f} Гц (Относительный пик: {db:.1f} дБ)"
        )
        top_peaks.append((frequency, db))

    request_str = "\n".join(request_lines) or "Выраженных пиков не найдено."
    bands_str = "\n".join(
        (
            f"Низкие (20-250 Гц): "
            f"{_format_energy(absolute_spectrum, frequencies, 20, 250)}",
            f"Средние (250-2к Гц): "
            f"{_format_energy(absolute_spectrum, frequencies, 250, 2000)}",
            f"Высокие (2к-16к Гц): "
            f"{_format_energy(absolute_spectrum, frequencies, 2000, 16000)}",
        )
    )

    loudness = pyln.Meter(sample_rate).integrated_loudness(mono_audio)
    sample_peak = float(np.max(np.abs(mono_audio)))
    max_peak_db = 20 * np.log10(sample_peak) if sample_peak > 0 else float("-inf")
    oversampled_audio = resample_poly(
        mono_audio,
        TRUE_PEAK_OVERSAMPLE,
        1,
    )
    true_peak = float(np.max(np.abs(oversampled_audio)))
    true_peak_db = 20 * np.log10(true_peak) if true_peak > 0 else float("-inf")
    rms = float(np.sqrt(np.mean(mono_audio**2)))
    rms_db = 20 * np.log10(rms) if rms > 0 else float("-inf")
    crest_factor = max_peak_db - rms_db
    plr = true_peak_db - loudness if np.isfinite(loudness) else None
    short_term_lufs_variance = _short_term_lufs(mono_audio, sample_rate)

    dynamics_lines = [
        f"LUFS: {loudness:.1f}",
        f"Пиковый уровень: {max_peak_db:.1f} dBFS",
        f"True Peak: {true_peak_db:.1f} dBTP",
        f"RMS: {rms_db:.1f} dBFS",
        f"Динамика (Crest Factor): {crest_factor:.1f} дБ",
        f"Стерео-ширина (уровень боковой части к центральной): "
        f"{stereo_width_pct:.1f}% ({stereo_width_db:.1f} дБ)",
        f"Фазовая корреляция: {phase_correlation:.3f}",
        f"Спектральный центроид: {spectralcentroid:.0f} Гц",
        f"PLR (True Peak минус LUFS): "
        f"{plr:.1f} дБ" if plr is not None else "PLR: нет данных",
        (
            f"Разброс LUFS между 3-секундными окнами: "
            f"{short_term_lufs_variance:.2f} дБ²"
            if short_term_lufs_variance is not None
            else "Разброс LUFS: трек короче двух полных 3-секундных окон."
        ),
    ]

    result = {
        "spectrum": normalized_spectrum,
        "absolute_spectrum": absolute_spectrum,
        "frequencies": frequencies,
        "request_str": request_str,
        "bands_str": bands_str,
        "dynamics_str": ". ".join(dynamics_lines) + ".",
        "loudness": loudness,
        "max_peak_db": max_peak_db,
        "true_peak_db": true_peak_db,
        "crest_factor": crest_factor,
        "plr": plr,
        "stereo_width_pct": stereo_width_pct,
        "stereo_width_db": stereo_width_db,
        "phase_correlation": phase_correlation,
        "spectralcentroid": spectralcentroid,
        "short_term_lufs_variance": short_term_lufs_variance,
        "top_peaks": top_peaks,
        "analysis_context": analysis_context,
        "segment_analyses": [],
    }
    if segments:
        for segment in _extract_segments(source_audio, sample_rate, segments):
            segment_result = _analyze_audio(
                segment["audio"],
                sample_rate,
            )
            result["segment_analyses"].append(
                {
                    "name": segment["name"],
                    "start": segment["start"],
                    "end": segment["end"],
                    "top_peaks": segment_result["top_peaks"],
                    "loudness": segment_result["loudness"],
                    "true_peak_db": segment_result["true_peak_db"],
                    "crest_factor": segment_result["crest_factor"],
                    "phase_correlation": segment_result["phase_correlation"],
                    "spectralcentroid": segment_result["spectralcentroid"],
                }
            )
    return result


@st.cache_data(show_spinner=False)
def _analyze_audio_cached(file_path, file_size, modified_ns, segments):
    try:
        audio, sample_rate = librosa.load(file_path, sr=None, mono=False)
    except Exception as error:
        raise ValueError(INVALID_AUDIO_FILE_MESSAGE) from error
    return _analyze_audio(audio, sample_rate, segments)


def analyze_audio_file(file_path, segments=()):
    resolved_path = str(Path(file_path).resolve())
    file_stat = Path(resolved_path).stat()
    segment_key = tuple(tuple(segment) for segment in segments)
    return _analyze_audio_cached(
        resolved_path,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        segment_key,
    )


def _find_conflict_zones(beat_data, vocal_data):
    beat_frequencies = beat_data["frequencies"]
    vocal_frequencies = vocal_data["frequencies"]
    beat_spectrum = beat_data["absolute_spectrum"]
    vocal_spectrum = vocal_data["absolute_spectrum"]
    lower_frequency = 80
    upper_frequency = min(12000, beat_frequencies[-1], vocal_frequencies[-1])

    shared_frequencies = beat_frequencies[
        (beat_frequencies >= lower_frequency)
        & (beat_frequencies <= upper_frequency)
    ]
    if len(shared_frequencies) < 3:
        return []

    beat_db = np.interp(shared_frequencies, beat_frequencies, beat_spectrum)
    vocal_db = np.interp(shared_frequencies, vocal_frequencies, vocal_spectrum)
    beat_relative_db = beat_db - np.max(beat_spectrum)
    vocal_relative_db = vocal_db - np.max(vocal_spectrum)
    overlap_strength = np.minimum(beat_relative_db, vocal_relative_db)
    overlap_strength = np.convolve(
        overlap_strength,
        np.ones(3) / 3,
        mode="same",
    )
    overlap_mask = (
        (beat_relative_db >= -24)
        & (vocal_relative_db >= -24)
        & (overlap_strength >= -24)
    )

    transitions = np.diff(np.pad(overlap_mask.astype(int), (1, 1)))
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    zones = []
    for start, end in zip(starts, ends):
        if end - start < 3:
            continue
        zones.append(
            {
                "start_hz": float(shared_frequencies[start]),
                "end_hz": float(shared_frequencies[min(end - 1, len(shared_frequencies) - 1)]),
                "strength_db": float(np.mean(overlap_strength[start:end])),
            }
        )

    return sorted(zones, key=lambda zone: zone["strength_db"], reverse=True)[:6]


def analyzestems(beat_path, vocal_path, segments=()):
    beat_data = analyze_audio_file(beat_path, segments)
    vocal_data = analyze_audio_file(vocal_path, segments)
    conflict_zones = _find_conflict_zones(beat_data, vocal_data)

    if conflict_zones:
        conflict_lines = [
            f"{zone['start_hz']:.0f}-{zone['end_hz']:.0f} Гц "
            f"(обе дорожки выражены в этой области)"
            for zone in conflict_zones
        ]
        conflict_str = (
            "Возможные зоны частотного наложения бита и вокала "
            "(это подсказки для прослушивания, а не доказательство проблемы):\n"
            + "\n".join(conflict_lines)
        )
    else:
        conflict_str = (
            "Выраженных общих зон частот не найдено. Проверь баланс на слух."
        )

    return {
        "beat": beat_data,
        "vocal": vocal_data,
        "conflict_zones": conflict_zones,
        "conflict_str": conflict_str,
        "analysis_context": (
            "Это отдельные стемы: бит и вокал анализируются по отдельности. "
            "Оценивай найденные зоны как возможное частотное наложение, "
            "а не как гарантированную проблему."
        ),
    }


def compare_spectra(current_audio, reference_audio):
    current_spectrum = current_audio.get(
        "absolute_spectrum",
        current_audio["spectrum"],
    )
    reference_spectrum = reference_audio.get(
        "absolute_spectrum",
        reference_audio["spectrum"],
    )
    current_frequencies = current_audio["frequencies"]
    reference_frequencies = reference_audio["frequencies"]
    delta_lines = []

    for lower, upper, label in FREQUENCY_BANDS:
        current_mask = (
            (current_frequencies >= lower) & (current_frequencies < upper)
        )
        reference_mask = (
            (reference_frequencies >= lower) & (reference_frequencies < upper)
        )
        if not np.any(current_mask) or not np.any(reference_mask):
            continue

        delta_db = float(
            np.mean(reference_spectrum[reference_mask])
            - np.mean(current_spectrum[current_mask])
        )
        if abs(delta_db) < 1:
            continue
        direction = "больше" if delta_db > 0 else "меньше"
        delta_lines.append(
            f"В области {label} у референса примерно на "
            f"{abs(delta_db):.1f} дБ {direction} энергии."
        )

    if not delta_lines:
        return (
            "Заметной разницы по широким частотным областям не обнаружено. "
            "Сравнение показывает относительный баланс тембра, а не "
            "абсолютную громкость треков."
        )

    return (
        "Сравнение спектров показывает относительный баланс тембра "
        "(не абсолютную громкость):\n" + "\n".join(delta_lines)
    )
