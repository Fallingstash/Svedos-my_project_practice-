import streamlit as st

from views.settings import ANALYSIS_MODE_LABELS


def render_onboarding(segments):
    st.write(
        "Добро пожаловать в Svedos. Загрузите свой трек, "
        "чтобы получить советы по сведению от ИИ."
    )
    analysis_mode = st.radio(
        "Что анализировать",
        ("master", "stems"),
        format_func=ANALYSIS_MODE_LABELS.get,
        key="onboarding_analysis_mode",
    )
    with st.form("upload_form"):
        uploaded_file = None
        beat_file = None
        vocal_file = None
        ref_file = None
        if analysis_mode == "master":
            uploaded_file = st.file_uploader(
                "Загрузите мастер-трек", type=["mp3", "wav"]
            )
            ref_file = st.file_uploader(
                "Загрузите референс (опционально)", type=["mp3", "wav"]
            )
        else:
            beat_file = st.file_uploader(
                "Загрузите стем бита", type=["mp3", "wav"]
            )
            vocal_file = st.file_uploader(
                "Загрузите стем вокала", type=["mp3", "wav"]
            )
        genre = st.selectbox("Жанр", ["Рэп", "Хип-Хоп", "Поп", "Лоу-фай"])
        analyze_clicked = st.form_submit_button("🚀 Начать анализ", type="primary")

    st.subheader("Сегменты трека (опционально)")
    with st.form("start_segment_form", clear_on_submit=True):
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
        st.rerun()

    if segments:
        for index, segment in enumerate(segments):
            col_description, col_delete = st.columns([4, 1])
            col_description.write(
                f"- {segment['name']}: {segment['start']} - {segment['end']} сек."
            )
            if col_delete.button("❌", key=f"start_del_{index}"):
                segments.pop(index)
                st.rerun()

    if analyze_clicked and analysis_mode == "master" and uploaded_file is not None:
        return {
            "analysis_mode": analysis_mode,
            "track": uploaded_file,
            "reference": ref_file,
            "genre": genre,
        }
    if analyze_clicked and analysis_mode == "master":
        st.error("Загрузите мастер-трек, чтобы начать анализ.")
    if analyze_clicked and analysis_mode == "stems":
        if beat_file is None or vocal_file is None:
            st.error("Загрузите оба файла: стем бита и стем вокала.")
        else:
            return {
                "analysis_mode": analysis_mode,
                "beat": beat_file,
                "vocal": vocal_file,
                "reference": None,
                "genre": genre,
            }
    return None
