from datetime import datetime
from pathlib import Path

import streamlit as st

from utils.session_store import list_sessions, load_session, rename_session


def _session_label(session):
    name = session["display_name"] or session["track_name"]
    try:
        created_at = datetime.fromisoformat(session["created_at"])
    except (TypeError, ValueError):
        return f"{name} · дата неизвестна"
    return f"{name} · {created_at.strftime('%d.%m.%Y %H:%M')}"


def _activate_session(session_id):
    session = load_session(session_id)
    if session is None:
        st.error("Не удалось найти выбранную сессию.")
        return

    required_paths = [session["track_path"]]
    if session["analysis_mode"] == "stems":
        required_paths.extend((session["beat_path"], session["vocal_path"]))
    missing_paths = [
        path for path in required_paths if not path or not Path(path).is_file()
    ]
    if missing_paths:
        st.error("Не найдены аудиофайлы этой сессии. Проверь папку history.")
        return

    st.session_state.session_id = session["id"]
    st.session_state.track_name = session["track_name"]
    st.session_state.display_name = session["display_name"]
    st.session_state.session_created_at = session["created_at"]
    st.session_state.genre = session["genre"]
    st.session_state.file_path = session["track_path"]
    st.session_state.ref_path = session["reference_path"]
    st.session_state.analysis_mode = session["analysis_mode"]
    st.session_state.analysis_mode_selector = session["analysis_mode"]
    st.session_state.beat_path = session["beat_path"]
    st.session_state.vocal_path = session["vocal_path"]
    st.session_state.segments = session["segments"]
    st.session_state.chat_history = session["chat_history"]
    st.session_state.analyzed = True
    st.session_state.pendingai_request = False
    st.session_state.pendingai_payload = None
    st.session_state.pendingai_error = None
    st.session_state.old_spectrum = None
    st.session_state.old_frequencies = None
    st.session_state.view_mode = "chat"
    st.session_state.chat_upload_generation += 1
    st.rerun()


def _start_new_session():
    defaults = {
        "analyzed": False,
        "chat_history": [],
        "old_spectrum": None,
        "old_frequencies": None,
        "segments": [],
        "genre": "Рэп",
        "file_path": "",
        "ref_path": None,
        "beat_path": None,
        "vocal_path": None,
        "analysis_mode": "master",
        "analysis_mode_selector": "master",
        "view_mode": "chat",
        "session_id": None,
        "track_name": "",
        "display_name": "",
        "session_created_at": None,
        "pendingai_request": False,
        "pendingai_payload": None,
        "pendingai_error": None,
        "renaming_session_id": None,
    }
    for key, value in defaults.items():
        st.session_state[key] = value.copy() if isinstance(value, list) else value
    st.session_state.chat_upload_generation += 1
    st.rerun()


def render_sidebar():
    with st.sidebar:
        if "renaming_session_id" not in st.session_state:
            st.session_state.renaming_session_id = None
        st.header("Мои сессии")
        if st.button("➕ Новая сессия", use_container_width=True):
            _start_new_session()

        for session in list_sessions():
            is_active = session["id"] == st.session_state.session_id
            label = _session_label(session)
            session_name = session["display_name"] or session["track_name"]
            button_col, rename_col = st.columns([8, 1])
            if button_col.button(
                f"{'▶ ' if is_active else ''}{label}",
                key=f"open_session_{session['id']}",
                use_container_width=True,
                help=session_name,
            ):
                _activate_session(session["id"])

            if rename_col.button(
                "✏️",
                key=f"rename_open_{session['id']}",
                help="Переименовать сессию",
            ):
                st.session_state.renaming_session_id = session["id"]
                st.rerun()

            if st.session_state.renaming_session_id == session["id"]:
                with st.form(f"rename_form_{session['id']}"):
                    edited_name = st.text_input("Название", value=session_name)
                    save_name = st.form_submit_button("Сохранить")
                if save_name:
                    try:
                        rename_session(session["id"], edited_name)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        if is_active:
                            st.session_state.display_name = edited_name.strip()
                        st.session_state.renaming_session_id = None
                        st.rerun()
