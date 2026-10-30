import streamlit as st

from views.segment_builder import render_segment_builder
from views.settings import ANALYSIS_MODE_LABELS, MAX_UPLOAD_SIZE_MB


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
                "Загрузите мастер-трек",
                type=["mp3", "wav"],
                max_upload_size=MAX_UPLOAD_SIZE_MB,
            )
            ref_file = st.file_uploader(
                "Загрузите референс (опционально)",
                type=["mp3", "wav"],
                max_upload_size=MAX_UPLOAD_SIZE_MB,
            )
        else:
            beat_file = st.file_uploader(
                "Загрузите стем бита",
                type=["mp3", "wav"],
                max_upload_size=MAX_UPLOAD_SIZE_MB,
            )
            vocal_file = st.file_uploader(
                "Загрузите стем вокала",
                type=["mp3", "wav"],
                max_upload_size=MAX_UPLOAD_SIZE_MB,
            )
        genre = st.selectbox("Жанр", ["Рэп", "Хип-Хоп", "Поп", "Лоу-фай"])
        analyze_clicked = st.form_submit_button("🚀 Начать анализ", type="primary")

    st.subheader("Сегменты трека (опционально)")
    if render_segment_builder(segments, "onboarding"):
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
