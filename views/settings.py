import streamlit as st


ANALYSIS_MODE_LABELS = {
    "master": "Анализировать мастер-трек",
    "stems": "Анализировать стемы (Бит + Вокал)",
}
MAX_UPLOAD_SIZE_MB = 30
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


def render_settings(segments):
    col_heading, col_close = st.columns([4, 1])
    col_heading.subheader("⚙️ Настройки трека")
    if col_close.button("❌ Отмена", key="close_settings"):
        return {"action": "close"}

    st.write("**Обновить трек / Добавить референс:**")
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
    with st.form("segment_form", clear_on_submit=True):
        segment_name = st.text_input("Название", "Куплет")
        col_start, col_end = st.columns(2)
        start_time = col_start.number_input("Начало (сек)", min_value=0, value=10)
        end_time = col_end.number_input("Конец (сек)", min_value=1, value=30)
        add_segment = st.form_submit_button("Добавить сегмент")

    if add_segment:
        if end_time <= start_time:
            st.error("Время окончания должно быть позже времени начала.")
            return None
        segments.append(
            {"name": segment_name, "start": start_time, "end": end_time}
        )
        return {"action": "segments_updated"}

    if segments:
        for index, segment in enumerate(segments):
            col_description, col_delete = st.columns([4, 1])
            col_description.write(
                f"- {segment['name']} ({segment['start']}-{segment['end']})"
            )
            if col_delete.button("❌", key=f"del_{index}"):
                segments.pop(index)
                return {"action": "segments_updated"}

    if apply_changes:
        return {
            "action": "apply",
            "analysis_mode": analysis_mode,
            "track": new_track,
            "reference": new_reference,
            "beat": new_beat,
            "vocal": new_vocal,
        }
    return None
