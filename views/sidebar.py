from datetime import datetime
from pathlib import Path

import streamlit as st

from utils.session_store import (
    delete_session,
    list_sessions,
    load_session,
    rename_session,
)


def _session_label(session):
    name = session["display_name"] or session["track_name"]
    try:
        created_at = datetime.fromisoformat(session["created_at"])
    except (TypeError, ValueError):
        return f"{name} · дата неизвестна"
    return f"{name} · {created_at.strftime('%d.%m.%Y %H:%M')}"


def _activate_session(session_id):
    session = load_session(session_id, st.session_state.user_id)
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
    st.session_state.vibe_description = session["vibe_description"]
    st.session_state.session_vibe_input = session["vibe_description"]
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
    st.session_state.sessionto_manage = None
    st.session_state.session_delete_confirmation_id = None
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
        "vibe_description": "",
        "session_vibe_input": None,
        "onboarding_vibe_input": "",
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
        "sessionto_manage": None,
        "session_delete_confirmation_id": None,
        "chat_upload_generation": 0,
    }
    for key, value in defaults.items():
        st.session_state[key] = value.copy() if isinstance(value, list) else value
    st.session_state.chat_upload_generation += 1
    st.rerun()


@st.dialog("Управление сессией")
def _manage_session_dialog(session_id):
    session = load_session(session_id, st.session_state.user_id)
    if session is None:
        st.error("Не удалось найти выбранную сессию.")
        if st.button("Закрыть", key="close_missing_session_dialog"):
            st.session_state.sessionto_manage = None
            st.rerun()
        return

    session_name = session["display_name"] or session["track_name"]
    edited_name = st.text_input(
        "Название сессии",
        value=session_name,
        key=f"manage_session_name_{session_id}",
    )
    open_col, save_col = st.columns(2)
    if open_col.button(
        "Открыть",
        key=f"manage_open_session_{session_id}",
        use_container_width=True,
    ):
        st.session_state.sessionto_manage = None
        _activate_session(session_id)

    if save_col.button(
        "Сохранить имя",
        key=f"manage_save_session_{session_id}",
        use_container_width=True,
    ):
        try:
            rename_session(session_id, st.session_state.user_id, edited_name)
        except ValueError as error:
            st.error(str(error))
        else:
            if session_id == st.session_state.session_id:
                st.session_state.display_name = edited_name.strip()
            st.session_state.sessionto_manage = None
            st.session_state.session_delete_confirmation_id = None
            st.rerun()

    if st.button(
        "Удалить",
        key=f"manage_delete_session_{session_id}",
        type="secondary",
        use_container_width=True,
    ):
        st.session_state.session_delete_confirmation_id = session_id

    if st.session_state.session_delete_confirmation_id == session_id:
        st.warning(f"Удалить диалог «{session_name}»? Это действие нельзя отменить.")
        confirm_col, cancel_delete_col = st.columns(2)
        if confirm_col.button(
            "Да, удалить",
            key=f"confirm_manage_delete_{session_id}",
            type="primary",
            use_container_width=True,
        ):
            try:
                delete_session(session_id, st.session_state.user_id)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state.sessionto_manage = None
                st.session_state.session_delete_confirmation_id = None
                if session_id == st.session_state.session_id:
                    _start_new_session()
                st.rerun()
        if cancel_delete_col.button(
            "Не удалять",
            key=f"cancel_manage_delete_{session_id}",
            use_container_width=True,
        ):
            st.session_state.session_delete_confirmation_id = None
            st.rerun()

    if st.button(
        "Отмена",
        key=f"cancel_manage_session_{session_id}",
        use_container_width=True,
    ):
        st.session_state.sessionto_manage = None
        st.session_state.session_delete_confirmation_id = None
        st.rerun()


def render_sidebar():
    with st.sidebar:
        if "sessionto_manage" not in st.session_state:
            st.session_state.sessionto_manage = None
        if "session_delete_confirmation_id" not in st.session_state:
            st.session_state.session_delete_confirmation_id = None
        st.header("Мои сессии")
        if st.button("➕ Новая сессия", use_container_width=True):
            _start_new_session()

        sessions = list_sessions(st.session_state.user_id)
        for session in sessions:
            is_active = session["id"] == st.session_state.session_id
            label = _session_label(session)
            if st.button(
                f"{'📍 ' if is_active else ''}{label}",
                key=f"open_session_{session['id']}",
                use_container_width=True,
            ):
                st.session_state[f"manage_session_name_{session['id']}"] = (
                    session["display_name"] or session["track_name"]
                )
                st.session_state.sessionto_manage = session["id"]
                st.session_state.session_delete_confirmation_id = None
                st.rerun()

    session_id_to_manage = st.session_state.sessionto_manage
    if session_id_to_manage:
        _manage_session_dialog(session_id_to_manage)
