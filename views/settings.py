import streamlit as st

from utils.audioutils import validate_audio_upload
from utils.config import MAX_UPLOAD_SIZE_MB
from views.segment_builder import render_segment_builder


def _build_markdown_report(track_name, genre, audio_data, stem_data, chat_history):
    lines = [
        f"# Отчёт по треку: {track_name}",
        "",
        f"**Жанр:** {genre}",
        "",
    ]
    if stem_data:
        for label, stem in (
            ("Бит", stem_data["beat"]),
            ("Вокал", stem_data["vocal"]),
        ):
            peaks = stem.get("top_peaks", [])[:3]
            peak_text = ", ".join(
                f"{frequency:.0f} Гц ({db:.1f} дБ)" for frequency, db in peaks
            ) or "не обнаружены"
            lines.extend(
                [
                    f"## {label}",
                    f"- Топ-3 пика: {peak_text}",
                    f"- LUFS: {stem['loudness']:.1f}",
                    f"- True Peak: {stem['true_peak_db']:.1f} dBTP",
                    "",
                ]
            )
    elif audio_data:
        peaks = audio_data.get("top_peaks", [])[:3]
        peak_text = ", ".join(
            f"{frequency:.0f} Гц ({db:.1f} дБ)" for frequency, db in peaks
        ) or "не обнаружены"
        lines.extend(
            [
                "## Метрики",
                f"- Топ-3 пика: {peak_text}",
                f"- LUFS: {audio_data['loudness']:.1f}",
                f"- True Peak: {audio_data['true_peak_db']:.1f} dBTP",
                "",
            ]
        )
    else:
        lines.extend(["## Метрики", "Данные анализа пока недоступны.", ""])

    lines.extend(["## История переписки", ""])
    for message in chat_history:
        speaker = "Вы" if message["role"] == "user" else "ИИ-наставник"
        lines.extend([f"### {speaker}", message["content"], ""])
    return "\n".join(lines)


ANALYSIS_MODE_LABELS = {
    "master": "Анализировать мастер-трек",
    "stems": "Анализировать стемы (Бит + Вокал)",
}


def render_settings(
    segments,
    current_vibe="",
    track_name="Трек",
    genre="",
    audio_data=None,
    stem_data=None,
    chat_history=(),
):
    col_heading, col_close = st.columns([4, 1])
    col_heading.subheader("⚙️ Настройки трека")
    if col_close.button("❌ Отмена", key="close_settings"):
        return {"action": "close"}

    st.write("**Обновить трек / Добавить референс:**")
    st.download_button(
        "📥 Скачать отчет (Markdown)",
        data=_build_markdown_report(
            track_name,
            genre,
            audio_data,
            stem_data,
            chat_history,
        ),
        file_name="svedos_report.md",
        mime="text/markdown",
        use_container_width=True,
    )
    if st.session_state.get("session_vibe_input") is None:
        st.session_state.session_vibe_input = current_vibe
    analysis_mode = st.radio(
        "Что анализировать",
        ("master", "stems"),
        format_func=ANALYSIS_MODE_LABELS.get,
        key="analysis_mode_selector",
    )
    new_track = None
    new_reference = st.file_uploader(
        "Новый референс",
        type=["mp3", "wav"],
        key="new_ref",
        max_upload_size=MAX_UPLOAD_SIZE_MB,
    )
    new_beat = None
    new_vocal = None
    if analysis_mode == "master":
        new_track = st.file_uploader(
            "Новая версия трека",
            type=["mp3", "wav"],
            key="new_track",
            max_upload_size=MAX_UPLOAD_SIZE_MB,
        )
    else:
        new_beat = st.file_uploader(
            "Стем бита",
            type=["mp3", "wav"],
            key="settings_beat",
            max_upload_size=MAX_UPLOAD_SIZE_MB,
        )
        new_vocal = st.file_uploader(
            "Стем вокала",
            type=["mp3", "wav"],
            key="settings_vocal",
            max_upload_size=MAX_UPLOAD_SIZE_MB,
        )
    vibe_description = st.text_area(
        "Опиши свои мысли о треке (вайб, замысел, что изменил)",
        placeholder="Расскажи о желаемом звучании, замысле или внесённых изменениях",
        max_chars=500,
        height=160,
        key="session_vibe_input",
    )
    apply_changes = st.button(
        "✅ Применить и вернуться в чат",
        type="primary",
        use_container_width=True,
    )

    st.divider()
    st.write("**Сегменты текущего трека:**")
    if render_segment_builder(segments, "settings"):
        return {"action": "segments_updated"}

    if apply_changes:
        upload_errors = {
            error
            for uploaded_file in (new_reference, new_track, new_beat, new_vocal)
            if (error := validate_audio_upload(uploaded_file)) is not None
        }
        if upload_errors:
            for error in upload_errors:
                st.error(error)
            return None
        return {
            "action": "apply",
            "analysis_mode": analysis_mode,
            "track": new_track,
            "reference": new_reference,
            "beat": new_beat,
            "vocal": new_vocal,
            "vibe_description": vibe_description.strip(),
        }
    return None
