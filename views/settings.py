import streamlit as st

from views.segment_builder import render_segment_builder


ANALYSIS_MODE_LABELS = {
    "master": "Анализировать мастер-трек",
    "stems": "Анализировать стемы (Бит + Вокал)",
}
MAX_UPLOAD_SIZE_MB = 30
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


def render_settings(segments, current_vibe=""):
    col_heading, col_close = st.columns([4, 1])
    col_heading.subheader("⚙️ Настройки трека")
    if col_close.button("❌ Отмена", key="close_settings"):
        return {"action": "close"}

    st.write("**Обновить трек / Добавить референс:**")
    if st.session_state.get("session_vibe_input") is None:
        st.session_state.session_vibe_input = current_vibe
    vibe_description = st.text_area(
        "Опиши свой вайб / цель",
        placeholder="Например: хочу мрачный трэп с грязным звуком",
        max_chars=500,
        key="session_vibe_input",
    )
    analysis_mode = st.radio(
        "Что анализировать",
        ("master", "stems"),
        format_func=ANALYSIS_MODE_LABELS.get,
        key="analysis_mode_selector",
    )
    new_track = st.file_uploader(
        "Новая версия трека",
        type=["mp3", "wav"],
        key="new_track",
        max_upload_size=MAX_UPLOAD_SIZE_MB,
    )
    new_reference = st.file_uploader(
        "Новый референс",
        type=["mp3", "wav"],
        key="new_ref",
        max_upload_size=MAX_UPLOAD_SIZE_MB,
    )
    new_beat = None
    new_vocal = None
    if analysis_mode == "stems":
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
