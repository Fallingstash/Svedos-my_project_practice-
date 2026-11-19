from datetime import datetime

import streamlit as st

from utils.config import get_server_secret
from utils.session_store import (
    create_share_token,
    delete_session,
    list_sessions,
    load_session,
    rename_session,
    revoke_share_token,
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

    st.session_state.session_id = session["id"]
    st.session_state.recovery_mode = False
    st.session_state.recovery_token = None
    st.query_params["sessionid"] = session["id"]
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
    st.session_state.attachedmetrics = []
    st.session_state.analyzed = True
    st.session_state.pendingai_request = False
    st.session_state.pendingai_payload = None
    st.session_state.pendingai_error = None
    st.session_state.sessionto_manage = None
    st.session_state.session_delete_confirmation_id = None
    st.session_state.old_spectrum = session["old_spectrum"]
    st.session_state.old_frequencies = session["old_frequencies"]
    st.session_state.view_mode = "chat"
    st.session_state.chat_upload_generation += 1
    st.rerun()


def _start_new_session():
    for parameter in ("sessionid", "restore", "share"):
        if parameter in st.query_params:
            del st.query_params[parameter]
    temporary_directory = st.session_state.get("audio_temp_dir")
    if temporary_directory is not None:
        try:
            temporary_directory.cleanup()
        except OSError as error:
            st.warning(f"Временные файлы аудио не удалось очистить: {error}")
    defaults = {
        "analyzed": False,
        "chat_history": [],
        "attachedmetrics": [],
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
        "session_save_pending": False,
        "session_save_error": None,
        "pendingai_request": False,
        "pendingai_payload": None,
        "pendingai_error": None,
        "sessionto_manage": None,
        "session_delete_confirmation_id": None,
        "chat_upload_generation": 0,
        "recovery_mode": False,
        "recovery_token": None,
        "readonly_mode": False,
        "share_token": None,
        "share_link": None,
        "share_confirmation_session_id": None,
        "file_refs": {},
        "file_metadata": [],
        "analysis_data": None,
        "context_summary": "",
        "context_summary_message_count": 0,
        "audio_runtime_cache": {},
        "audio_temp_dir": None,
    }
    for key, value in defaults.items():
        st.session_state[key] = (
            value.copy() if isinstance(value, (list, dict)) else value
        )
    st.session_state.chat_upload_generation += 1
    st.rerun()


def startnew_session():
    _start_new_session()


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
    st.divider()
    st.write("**Публикация**")
    share_col, revoke_col = st.columns(2)
    if share_col.button(
        "Поделиться сессией",
        key=f"share_session_{session_id}",
        use_container_width=True,
    ):
        st.session_state.share_confirmation_session_id = session_id
    if st.session_state.get("share_confirmation_session_id") == session_id:
        st.warning(
            "Любой обладатель ссылки сможет просматривать чат, метрики, "
            "графики и загруженное аудио этой сессии."
        )
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button(
            "Создать ссылку",
            key=f"confirm_share_{session_id}",
            use_container_width=True,
        ):
            app_base_url = get_server_secret("APPBASEURL")
            if not app_base_url:
                st.error(
                    "APPBASEURL не настроен. Сначала задайте публичный адрес приложения."
                )
            else:
                try:
                    token = create_share_token(
                        session_id,
                        st.session_state.user_id,
                    )
                except (ValueError, RuntimeError) as error:
                    st.error(str(error))
                else:
                    st.session_state.share_link = (
                        f"{app_base_url.rstrip('/')}/?share={token}"
                    )
                    st.session_state.share_confirmation_session_id = None
                    st.success("Публичная ссылка создана.")
        if cancel_col.button(
            "Отмена",
            key=f"cancel_share_{session_id}",
            use_container_width=True,
        ):
            st.session_state.share_confirmation_session_id = None
    if st.session_state.get("share_link"):
        st.text_input(
            "Ссылка для копирования",
            value=st.session_state.share_link,
            key=f"share_link_field_{session_id}",
        )
    if revoke_col.button(
        "Отозвать ссылку",
        key=f"revoke_share_{session_id}",
        use_container_width=True,
    ):
        try:
            revoke_share_token(session_id, st.session_state.user_id)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.session_state.share_link = None
            st.success("Публичная ссылка отозвана.")
    with st.form(key=f"manage_rename_form_{session_id}"):
        edited_name = st.text_input(
            "Название сессии",
            value=session_name,
        )
        save_name_clicked = st.form_submit_button(
            "Сохранить имя",
            use_container_width=True,
        )

    if st.button(
        "Открыть",
        key=f"manage_open_session_{session_id}",
        use_container_width=True,
    ):
        st.session_state.sessionto_manage = None
        _activate_session(session_id)

    if save_name_clicked:
        try:
            rename_session(session_id, st.session_state.user_id, edited_name)
        except (ValueError, RuntimeError) as error:
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
            except (ValueError, RuntimeError, OSError) as error:
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
        if st.session_state.get("readonly_mode", False):
            st.caption("Опубликованная сессия · Только просмотр")
            if st.button("Создать собственный разбор", use_container_width=True):
                _start_new_session()
            return
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
                st.session_state.sessionto_manage = session["id"]
                st.session_state.session_delete_confirmation_id = None
                st.rerun()

    session_id_to_manage = st.session_state.sessionto_manage
    if session_id_to_manage:
        _manage_session_dialog(session_id_to_manage)
