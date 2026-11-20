import random
import math
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st
import extra_streamlit_components as stx

from utils.config import (
    AUDIO_FACTS,
    INVALID_AUDIO_TYPE_MESSAGE,
    MAX_CHAT_MESSAGE_CHARS,
    MAX_UPLOAD_SIZE_BYTES,
    get_message_limit,
    MESSAGE_LIMIT_MESSAGE,
    MESSAGE_TOO_LONG_MESSAGE,
    RATE_LIMIT_MESSAGE,
    SUPPORTED_AUDIO_EXTENSIONS,
    UPLOAD_TOO_LARGE_MESSAGE,
)
from utils.aiutils import (
    AIRequestError,
    get_consolidation_prompt,
    get_strict_prompt,
    request_ai_response,
)
from utils.audioutils import (
    analyzestems,
    analyze_audio_file,
    compare_spectra,
    get_genre_reference_delta,
)
from utils.session_store import (
    allow_ai_request,
    load_shared_session,
    load_session,
    record_ai_usage,
    save_analysis,
    save_session,
)
from utils.guest_identity import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    get_guest_cookie_secret,
    sign_guest_id,
    resolve_guest_identity,
)
from utils.config import validate_storage_configuration
from utils.storage import (
    AudioStorageError,
    authorize_session_audio,
    materialize_audio,
    save_audio_upload,
    validate_backend_ready,
)
from views.chat import render_chat
from views.graph import render_graph
from views.onboarding import render_onboarding
from views.settings import render_settings
from views.sidebar import render_sidebar


st.set_page_config(page_title="Svedos", layout="wide")

SESSION_DEFAULTS = {
    "analyzed": False,
    "chat_history": [],
    "attachedmetrics": [],
    "old_spectrum": None,
    "old_frequencies": None,
    "segments": [],
    "genre": "Рэп",
    "vibe_description": "",
    "session_vibe_input": None,
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
    "user_id": None,
    "session_save_pending": False,
    "session_save_error": None,
    "pendingai_request": False,
    "pendingai_payload": None,
    "pendingai_error": None,
    "chat_upload_generation": 0,
    "sessionto_manage": None,
    "session_delete_confirmation_id": None,
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
    "last_ai_metadata": None,
    "audio_runtime_cache": {},
    "audio_temp_dir": None,
}
_COOKIE_MANAGER_LOADING = {"__svedos_cookie_manager_loading__": True}
for key, default in SESSION_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = (
            default.copy() if isinstance(default, (list, dict)) else default
        )


@st.cache_resource
def _validate_backend():
    validate_storage_configuration()
    validate_backend_ready()


try:
    _validate_backend()
except RuntimeError as error:
    st.error(str(error))
    st.stop()

if not st.session_state.user_id:
    try:
        guest_secret = get_guest_cookie_secret()
    except RuntimeError as error:
        st.error(str(error))
        st.stop()
    cookie_manager = stx.CookieManager(key="svedos_cookie_manager")
    signed_cookie = st.context.cookies.get(COOKIE_NAME)
    component_cookies = cookie_manager.cookie_manager(
        method="getAll",
        key="svedos_guest_identity_probe",
        default=_COOKIE_MANAGER_LOADING,
    )
    component_ready = (
        isinstance(component_cookies, dict)
        and component_cookies != _COOKIE_MANAGER_LOADING
    )
    component_cookie = (
        component_cookies.get(COOKIE_NAME)
        if component_ready
        else None
    )
    guest_id, identity_source = resolve_guest_identity(
        signed_cookie,
        component_cookie,
        component_ready,
        guest_secret,
    )
    if guest_id is None:
        st.info("Восстанавливаем гостевую сессию…")
        st.stop()
    if identity_source == "created":
        cookie_manager.set(
            COOKIE_NAME,
            sign_guest_id(guest_id, guest_secret),
            max_age=COOKIE_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            same_site="lax",
        )
    st.session_state.user_id = guest_id

st.markdown(
    """
    <style>
        .block-container { padding-top: 2rem; }
        h1 { margin-bottom: 40px; }
        [data-testid="column"] { padding: 0 5px !important; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("🎧 Svedos")


def analysis_segments():
    return tuple(
        (segment["name"], float(segment["start"]), float(segment["end"]))
        for segment in st.session_state.segments
    )


def _json_safe(value):
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _analysis_snapshot(audio_data, stem_data):
    fields = (
        "analysis_context",
        "request_str",
        "bands_str",
        "dynamics_str",
        "loudness",
        "max_short_term_lufs",
        "true_peak_db",
        "max_peak_db",
        "phase_correlation",
        "spectralcentroid",
        "crest_factor",
        "stereo_width_pct",
        "plr",
        "short_term_lufs_variance",
        "top_peaks",
        "spectrum",
        "frequencies",
        "segment_analyses",
    )
    snapshot = {
        key: _json_safe(audio_data[key])
        for key in fields
        if key in audio_data
    }
    if stem_data:
        snapshot["stems"] = _json_safe(stem_data)
    return snapshot


def _audio_temp_directory():
    temporary_directory = st.session_state.get("audio_temp_dir")
    if temporary_directory is None:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="svedos-audio-"
        )
        st.session_state.audio_temp_dir = temporary_directory
    return Path(temporary_directory.name)


def save_versioned_upload(uploaded_file, prefix):
    extension = Path(uploaded_file.name).suffix.lower()
    content_type = str(getattr(uploaded_file, "type", "") or "").lower()
    if (
        extension not in SUPPORTED_AUDIO_EXTENSIONS
        or not content_type.startswith("audio/")
    ):
        raise ValueError(INVALID_AUDIO_TYPE_MESSAGE)
    file_buffer = uploaded_file.getbuffer()
    if file_buffer.nbytes > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(UPLOAD_TOO_LARGE_MESSAGE)

    if not st.session_state.session_id:
        st.session_state.session_id = str(uuid4())
        st.session_state.session_created_at = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
    try:
        object_key, metadata = save_audio_upload(
            st.session_state.user_id,
            st.session_state.session_id,
            uploaded_file,
            prefix,
        )
        if not any(
            item["object_key"] == object_key
            for item in st.session_state.file_metadata
        ):
            st.session_state.file_metadata.append(metadata)
        if object_key.startswith(f"{st.session_state.user_id}/"):
            file_path = _audio_temp_directory() / (
                f"{uuid4().hex}{extension}"
            )
            with file_path.open("xb") as audio_file:
                audio_file.write(file_buffer)
            st.session_state.audio_runtime_cache[object_key] = str(file_path)
            return str(file_path), object_key
        return object_key, object_key
    except (AudioStorageError, OSError) as error:
        st.error(f"Не удалось сохранить аудио: {error}")
        st.stop()


def persist_current_session():
    if st.session_state.get("readonly_mode", False):
        return False
    if not st.session_state.session_id or not st.session_state.file_path:
        return False
    session_data = {
        "id": st.session_state.session_id,
        "user_id": st.session_state.user_id,
        "track_name": st.session_state.track_name,
        "display_name": st.session_state.display_name or st.session_state.track_name,
        "genre": st.session_state.genre,
        "vibe_description": st.session_state.vibe_description,
        "created_at": st.session_state.session_created_at,
        "track_path": st.session_state.file_refs.get(
            "track_path",
            st.session_state.file_path,
        ),
        "reference_path": st.session_state.file_refs.get(
            "reference_path",
            st.session_state.ref_path,
        ),
        "analysis_mode": st.session_state.analysis_mode,
        "beat_path": st.session_state.file_refs.get(
            "beat_path",
            st.session_state.beat_path,
        ),
        "vocal_path": st.session_state.file_refs.get(
            "vocal_path",
            st.session_state.vocal_path,
        ),
        "segments": st.session_state.segments,
        "chat_history": st.session_state.chat_history,
        "analysis_data": st.session_state.analysis_data,
        "context_summary": st.session_state.context_summary,
        "context_summary_message_count": (
            st.session_state.context_summary_message_count
        ),
        "file_metadata": st.session_state.file_metadata,
        "old_spectrum": (
            st.session_state.old_spectrum.tolist()
            if st.session_state.old_spectrum is not None
            else None
        ),
        "old_frequencies": (
            st.session_state.old_frequencies.tolist()
            if st.session_state.old_frequencies is not None
            else None
        ),
    }
    try:
        save_session(session_data, owner_id=st.session_state.user_id)
    except (RuntimeError, ValueError, PermissionError, OSError) as error:
        st.session_state.session_save_pending = True
        st.session_state.session_save_error = str(error)
        return False
    st.query_params["sessionid"] = st.session_state.session_id
    st.session_state.session_save_pending = False
    st.session_state.session_save_error = None
    return True


def _session_audio_path(session_id, field, object_key, share_token=None):
    if not object_key:
        return None
    user_id = st.session_state.user_id
    authorize_session_audio(
        session_id,
        object_key,
        user_id=None if share_token else user_id,
        share_token=share_token,
    )
    cached_path = st.session_state.audio_runtime_cache.get(object_key)
    if cached_path and Path(cached_path).is_file():
        return cached_path
    path = materialize_audio(
        session_id,
        object_key,
        _audio_temp_directory(),
        user_id=None if share_token else user_id,
        share_token=share_token,
    )
    st.session_state.audio_runtime_cache[object_key] = path
    return path


def _restore_session(session, readonly=False, share_token=None):
    same_private_session = (
        not readonly
        and not st.session_state.get("readonly_mode", False)
        and st.session_state.get("session_id") == session["id"]
    )
    st.session_state.session_id = session["id"]
    st.session_state.track_name = session["track_name"]
    st.session_state.display_name = session["display_name"]
    st.session_state.session_created_at = session["created_at"]
    st.session_state.genre = session["genre"]
    st.session_state.vibe_description = session["vibe_description"]
    st.session_state.session_vibe_input = session["vibe_description"]
    st.session_state.analysis_mode = session["analysis_mode"]
    st.session_state.analysis_mode_selector = session["analysis_mode"]
    st.session_state.segments = session["segments"]
    st.session_state.file_refs = {
        "track_path": session["track_path"],
        "reference_path": session["reference_path"],
        "beat_path": session["beat_path"],
        "vocal_path": session["vocal_path"],
    }
    st.session_state.file_metadata = session.get("file_metadata", [])
    st.session_state.file_path = _session_audio_path(
        session["id"],
        "track_path",
        session["track_path"],
        share_token,
    )
    st.session_state.ref_path = _session_audio_path(
        session["id"],
        "reference_path",
        session["reference_path"],
        share_token,
    )
    st.session_state.beat_path = _session_audio_path(
        session["id"],
        "beat_path",
        session["beat_path"],
        share_token,
    )
    st.session_state.vocal_path = _session_audio_path(
        session["id"],
        "vocal_path",
        session["vocal_path"],
        share_token,
    )
    st.session_state.chat_history = session["chat_history"]
    st.session_state.old_spectrum = session["old_spectrum"]
    st.session_state.old_frequencies = session["old_frequencies"]
    st.session_state.analysis_data = session.get("analysis_data")
    st.session_state.context_summary = session.get("context_summary", "")
    st.session_state.context_summary_message_count = session.get(
        "context_summary_message_count",
        0,
    )
    st.session_state.readonly_mode = readonly
    st.session_state.share_token = share_token
    st.session_state.recovery_mode = False
    st.session_state.recovery_token = None
    st.session_state.attachedmetrics = []
    st.session_state.analyzed = True
    if not same_private_session or readonly:
        st.session_state.pendingai_request = False
        st.session_state.pendingai_payload = None
        st.session_state.pendingai_error = None
    st.session_state.view_mode = "chat"


def restore_session_from_url():
    share_token = st.query_params.get("share")
    session_id = st.query_params.get("sessionid")

    if share_token:
        session = load_shared_session(share_token)
        if session is None:
            st.error(
                "Нет доступа к этой сессии. Возможно, она приватная или "
                "ссылка больше не действует."
            )
            st.stop()
        try:
            _restore_session(session, readonly=True, share_token=share_token)
        except (AudioStorageError, PermissionError, OSError) as error:
            st.error(f"Не удалось открыть аудио сессии: {error}")
            st.stop()
        return

    if st.session_state.get("readonly_mode", False):
        from views.sidebar import startnew_session

        startnew_session()

    if session_id:
        session = load_session(session_id, st.session_state.user_id)
        if session is None:
            st.error(
                "Нет доступа к этой сессии. Возможно, она приватная или "
                "ссылка больше не действует."
            )
            st.stop()
        try:
            _restore_session(session)
        except (AudioStorageError, PermissionError, OSError) as error:
            st.error(f"Не удалось открыть аудио сессии: {error}")
            st.stop()


def get_reference_data():
    reference_path = st.session_state.ref_path
    if not reference_path:
        return None
    if not Path(reference_path).is_file():
        st.warning(
            "Файл референса не найден; анализ основного трека продолжен без него."
        )
        st.session_state.ref_path = None
        persist_current_session()
        return None
    try:
        return analyze_audio_file(reference_path)
    except ValueError as error:
        st.warning(
            f"Референс не удалось прочитать; анализ продолжен без него. {error}"
        )
        st.session_state.ref_path = None
        persist_current_session()
        return None


def analyze_current_source():
    segments = analysis_segments()
    if st.session_state.analysis_mode == "stems":
        beat_path = st.session_state.beat_path
        vocal_path = st.session_state.vocal_path
        if not beat_path or not vocal_path:
            raise ValueError(
                "Для анализа стемов нужны оба файла: бит и вокал. "
                "Добавь их в настройках или в чате."
            )
        if not Path(beat_path).is_file() or not Path(vocal_path).is_file():
            raise ValueError("Не найден файл бита или вокала этой сессии.")

        stems = analyzestems(beat_path, vocal_path, segments)
        beat_data = stems["beat"]
        vocal_data = stems["vocal"]
        segment_analyses = [
            {
                "name": beat_segment["name"],
                "start": beat_segment["start"],
                "end": beat_segment["end"],
                "beat": beat_segment,
                "vocal": vocal_segment,
            }
            for beat_segment, vocal_segment in zip(
                beat_data["segment_analyses"],
                vocal_data["segment_analyses"],
            )
        ]
        combined_data = {
            **beat_data,
            "segment_analyses": segment_analyses,
            "request_str": (
                "Бит:\n"
                f"{beat_data['request_str']}\n"
                "Вокал:\n"
                f"{vocal_data['request_str']}"
            ),
            "bands_str": (
                "Бит:\n"
                f"{beat_data['bands_str']}\n"
                "Вокал:\n"
                f"{vocal_data['bands_str']}"
            ),
            "dynamics_str": (
                "Метрики бита:\n"
                f"{beat_data['dynamics_str']}\n"
                "Метрики вокала:\n"
                f"{vocal_data['dynamics_str']}"
            ),
            "analysis_context": (
                f"{stems['analysis_context']} "
                f"Бит: {beat_data['analysis_context']} "
                f"Вокал: {vocal_data['analysis_context']}"
            ),
        }
        return combined_data, stems

    track_path = st.session_state.file_path
    if not track_path or not Path(track_path).is_file():
        raise ValueError("Файл мастер-трека этой сессии не найден.")
    return analyze_audio_file(track_path, segments), None


def get_reference_delta(current_audio):
    if st.session_state.analysis_mode == "stems":
        return (
            "Референс не сравнивается со стемами: это разные типы материала. "
            "Сравни отдельно, прослушивая итоговый микс."
        )
    reference_data = get_reference_data()
    if reference_data is None:
        return ""
    comparison = compare_spectra(current_audio, reference_data)
    if st.session_state.segments:
        return (
            "Референс проанализирован целиком, а текущий трек — только по "
            "выбранным фрагментам. "
            + comparison
        )
    return comparison


def request_analysis(
    question,
    audio_data,
    chat_history=None,
    reference_delta="",
    stem_data=None,
    new_version=False,
):
    analysis_mode = st.session_state.analysis_mode
    analysis_context = audio_data["analysis_context"]
    if new_version:
        analysis_context = (
            "Я только что загрузил НОВУЮ версию трека после правок. "
            "Вот новые данные:\n"
            f"Пики: {audio_data['request_str']}\n"
            f"Баланс частот: {audio_data['bands_str']}\n"
            f"Динамика: {audio_data['dynamics_str']}\n"
            "Используй измерения текущего файла как "
            "основные, не подменяй их предыдущими значениями из истории и не "
            "повторяй старые советы без подтверждения новыми данными.\n"
            + analysis_context
        )
    stem_context = None
    if stem_data is not None:
        stem_context = (
            f"{stem_data['analysis_context']}\n"
            f"{stem_data['conflict_str']}"
        )
    system_prompt, user_prompt = get_strict_prompt(
        question=question,
        segments_list=st.session_state.segments,
        genre_name=st.session_state.genre,
        request_data=audio_data["request_str"],
        bands_data=audio_data["bands_str"],
        dynamics_data=audio_data["dynamics_str"],
        analysis_context=analysis_context,
        reference_delta=reference_delta,
        analysis_mode=analysis_mode,
        stem_data=stem_context,
        segment_data=audio_data.get("segment_analyses"),
        genre_delta=get_genre_reference_delta(audio_data, st.session_state.genre),
        vibe_description=st.session_state.vibe_description,
    )
    return _request_ai_with_diagnostics(
        system_prompt,
        user_prompt,
        chat_history,
    )


def _request_ai_with_diagnostics(system_prompt, user_prompt, chat_history=None):
    metadata = st.session_state.get("last_ai_metadata") or {}

    def persist_diagnostics():
        if not st.session_state.session_id or not metadata:
            return
        try:
            record_ai_usage(
                st.session_state.user_id,
                st.session_state.session_id,
                metadata.get("model"),
                metadata.get("prompt_tokens"),
                metadata.get("completion_tokens"),
                metadata.get("latency_ms", 0),
                metadata.get("attempts", 1),
            )
        except RuntimeError as error:
            st.warning(
                "Ответ получен, но диагностические данные AI не сохранены: "
                f"{error}"
            )

    try:
        answer = request_ai_response(system_prompt, user_prompt, chat_history)
    except AIRequestError:
        metadata = st.session_state.get("last_ai_metadata") or {}
        persist_diagnostics()
        raise
    metadata = st.session_state.get("last_ai_metadata") or {}
    persist_diagnostics()
    return answer


def process_pending_ai_request(payload):
    if st.session_state.get("readonly_mode", False):
        raise ValueError("Опубликованная сессия доступна только для просмотра.")
    if payload["kind"] == "consolidation":
        system_prompt, user_prompt = get_consolidation_prompt(
            st.session_state.genre
        )
        st.markdown(f"💡 **Факт о звуке:** {random.choice(AUDIO_FACTS)}")
        return _request_ai_with_diagnostics(
            system_prompt,
            user_prompt,
            st.session_state.chat_history[:-1],
        )

    st.caption("Слушаю ваш трек...")
    previous_track_path = payload.get("previous_track_path")
    if previous_track_path:
        previous_audio = analyze_audio_file(
            previous_track_path,
            analysis_segments(),
        )
        st.session_state.old_spectrum = previous_audio["spectrum"]
        st.session_state.old_frequencies = previous_audio["frequencies"]
        persist_current_session()

    st.caption("Считаю частоты...")
    audio_data, stem_data = analyze_current_source()
    if payload.get("new_version") and previous_track_path:
        previous_peaks = previous_audio.get("top_peaks", [])
        current_peaks = audio_data.get("top_peaks", [])
        if previous_peaks and current_peaks:
            comparison_message = (
                "📊 Сравнение версий: Главный пик сместился с "
                f"{previous_peaks[0][0]:.0f} Гц на "
                f"{current_peaks[0][0]:.0f} Гц. Это значит, что тональный "
                "баланс в области этого пика изменился; сравни версии на "
                "одинаковой громкости и проверь, поддерживает ли новое "
                "звучание твой замысел."
            )
            st.session_state.chat_history.insert(
                len(st.session_state.chat_history) - 1,
                {"role": "system", "content": comparison_message},
            )
            persist_current_session()
    reference_delta = get_reference_delta(audio_data)
    st.markdown(f"💡 **Факт о звуке:** {random.choice(AUDIO_FACTS)}")
    st.caption("ИИ анализирует трек...")
    return request_analysis(
        payload["question"],
        audio_data,
        st.session_state.chat_history[:-1],
        reference_delta,
        stem_data,
        payload.get("new_version", False),
    )


def queue_ai_request(
    question,
    kind,
    previous_track_path=None,
    new_version=False,
):
    if st.session_state.get("readonly_mode", False):
        st.session_state.pendingai_error = (
            "Опубликованная сессия доступна только для просмотра."
        )
        st.session_state.view_mode = "chat"
        return False
    if kind == "chat" and len(question) > MAX_CHAT_MESSAGE_CHARS:
        st.session_state.pendingai_error = MESSAGE_TOO_LONG_MESSAGE
        st.session_state.view_mode = "chat"
        return False

    message_count = sum(
        message["role"] == "user"
        for message in st.session_state.chat_history
    )
    message_limit = get_message_limit()
    if message_count >= message_limit:
        st.session_state.pendingai_error = MESSAGE_LIMIT_MESSAGE
        st.session_state.view_mode = "chat"
        return False

    if not persist_current_session():
        return False

    if not allow_ai_request(
        st.session_state.user_id,
        st.context.ip_address,
    ):
        st.session_state.pendingai_error = RATE_LIMIT_MESSAGE
        st.session_state.view_mode = "chat"
        return False

    st.session_state.pendingai_payload = {
        "kind": kind,
        "question": question,
        "previous_track_path": previous_track_path,
        "new_version": new_version,
    }
    st.session_state.pendingai_request = True
    st.session_state.view_mode = "chat"
    return True


if st.session_state.get("session_save_pending", False):
    st.error(
        "Сессия или метаданные аудио ещё не сохранены. "
        "Проверьте доступность хранилища и повторите попытку. "
        "Не закрывайте вкладку до успешного сохранения."
        + (
            f"\n\n{st.session_state.session_save_error}"
            if st.session_state.get("session_save_error")
            else ""
        )
    )
    if st.button("Повторить сохранение", type="primary"):
        if persist_current_session():
            st.rerun()
    st.stop()


restore_session_from_url()
if st.session_state.get("readonly_mode", False):
    st.warning("Опубликованная сессия · Только просмотр")
render_sidebar()


if not st.session_state.analyzed:
    onboarding_result = render_onboarding(st.session_state.segments)
    if onboarding_result:
        st.session_state.session_id = str(uuid4())
        st.session_state.session_created_at = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        st.session_state.attachedmetrics = []
        st.session_state.genre = onboarding_result["genre"]
        st.session_state.vibe_description = onboarding_result.get(
            "vibe_description",
            "",
        )
        st.session_state.session_vibe_input = st.session_state.vibe_description
        st.session_state.chat_history = []
        st.session_state.old_spectrum = None
        st.session_state.old_frequencies = None
        st.session_state.analysis_data = None
        st.session_state.file_refs = {}
        st.session_state.file_metadata = []
        st.session_state.analysis_mode = onboarding_result.get(
            "analysis_mode",
            st.session_state.get("onboarding_analysis_mode", "master"),
        )
        if st.session_state.analysis_mode == "master":
            track = onboarding_result["track"]
            st.session_state.track_name = track.name
            st.session_state.display_name = track.name
            (
                st.session_state.file_path,
                st.session_state.file_refs["track_path"],
            ) = save_versioned_upload(track, "track")
            st.session_state.beat_path = None
            st.session_state.vocal_path = None
            reference = onboarding_result["reference"]
            if reference is not None:
                (
                    st.session_state.ref_path,
                    st.session_state.file_refs["reference_path"],
                ) = save_versioned_upload(reference, "reference")
            else:
                st.session_state.ref_path = None
            initial_question = "Дай мне 3 конкретных совета по сведению этого трека."
        else:
            beat = onboarding_result["beat"]
            vocal = onboarding_result["vocal"]
            (
                st.session_state.beat_path,
                st.session_state.file_refs["beat_path"],
            ) = save_versioned_upload(beat, "beat")
            (
                st.session_state.vocal_path,
                st.session_state.file_refs["vocal_path"],
            ) = save_versioned_upload(vocal, "vocal")
            st.session_state.file_path = st.session_state.beat_path
            st.session_state.file_refs["track_path"] = (
                st.session_state.file_refs["beat_path"]
            )
            st.session_state.track_name = f"{beat.name} + {vocal.name}"
            st.session_state.display_name = st.session_state.track_name
            st.session_state.ref_path = None
            initial_question = (
                "Проанализируй стемы бита и вокала, найди возможные конфликты "
                "и дай конкретные советы, как освободить место для голоса."
            )
        st.session_state.analyzed = True
        initial_question = (
            f"{initial_question}\nМои мысли о треке: "
            f"{st.session_state.vibe_description}"
            if st.session_state.vibe_description
            else initial_question
        )
        queue_ai_request(
            initial_question,
            "initial",
        )
        st.rerun()
else:
    try:
        current_audio, current_stem_data = analyze_current_source()
        reference_audio = get_reference_data()
        analysis_error = None
    except ValueError as error:
        current_audio = None
        current_stem_data = None
        reference_audio = None
        analysis_error = str(error)

    if (
        current_audio is not None
        and not st.session_state.get("readonly_mode", False)
        and st.session_state.analysis_data is None
    ):
        analysis_snapshot = _analysis_snapshot(
            current_audio,
            current_stem_data,
        )
        try:
            save_analysis(
                st.session_state.session_id,
                st.session_state.user_id,
                analysis_snapshot,
            )
        except (RuntimeError, ValueError, OSError) as error:
            st.error(f"Не удалось сохранить результаты анализа: {error}")
            st.stop()
        st.session_state.analysis_data = analysis_snapshot

    col_chat, col_graph = st.columns([1, 1])

    with col_chat:
        if st.session_state.get("readonly_mode", False):
            st.session_state.view_mode = "chat"
        if st.session_state.view_mode == "settings":
            settings_result = render_settings(
                st.session_state.segments,
                st.session_state.vibe_description,
                track_name=(
                    st.session_state.display_name
                    or st.session_state.track_name
                ),
                genre=st.session_state.genre,
                audio_data=current_audio,
                stem_data=current_stem_data,
                chat_history=st.session_state.chat_history,
            )

            if settings_result and settings_result["action"] == "segments_updated":
                st.session_state.analysis_data = None
                persist_current_session()
                st.rerun()

            if settings_result and settings_result["action"] == "close":
                st.session_state.view_mode = "chat"
                st.rerun()

            if (
                settings_result
                and settings_result["action"] == "apply"
                and st.session_state.pendingai_request
            ):
                st.warning(
                    "Дождитесь ответа ИИ, чтобы загрузить новую версию"
                )

            if (
                settings_result
                and settings_result["action"] == "apply"
                and not st.session_state.pendingai_request
            ):
                selected_mode = settings_result["analysis_mode"]
                if selected_mode == "master":
                    has_master = settings_result["track"] is not None or (
                        st.session_state.analysis_mode == "master"
                        and st.session_state.file_path
                        and Path(st.session_state.file_path).is_file()
                    )
                    if not has_master:
                        st.error("Для режима мастер-трека загрузи мастер-трек.")
                        st.stop()
                else:
                    has_beat = settings_result["beat"] is not None or (
                        st.session_state.analysis_mode == "stems"
                        and st.session_state.beat_path
                        and Path(st.session_state.beat_path).is_file()
                    )
                    has_vocal = settings_result["vocal"] is not None or (
                        st.session_state.analysis_mode == "stems"
                        and st.session_state.vocal_path
                        and Path(st.session_state.vocal_path).is_file()
                    )
                    if not has_beat or not has_vocal:
                        st.error("Для режима стемов загрузи и бит, и вокал.")
                        st.stop()

                st.session_state.vibe_description = settings_result[
                    "vibe_description"
                ]
                old_track_path = st.session_state.file_path
                new_version_uploaded = (
                    settings_result["track"] is not None
                    if settings_result["analysis_mode"] == "master"
                    else any(
                        settings_result[file_key] is not None
                        for file_key in ("beat", "vocal")
                    )
                )
                if settings_result["track"] is not None:
                    old_track_name = st.session_state.track_name
                    st.session_state.track_name = settings_result["track"].name
                    if (
                        not st.session_state.display_name
                        or st.session_state.display_name == old_track_name
                    ):
                        st.session_state.display_name = settings_result["track"].name
                    (
                        st.session_state.file_path,
                        st.session_state.file_refs["track_path"],
                    ) = save_versioned_upload(
                        settings_result["track"],
                        "track",
                    )

                if settings_result["reference"] is not None:
                    (
                        st.session_state.ref_path,
                        st.session_state.file_refs["reference_path"],
                    ) = save_versioned_upload(
                        settings_result["reference"],
                        "reference",
                    )

                if settings_result["beat"] is not None:
                    (
                        st.session_state.beat_path,
                        st.session_state.file_refs["beat_path"],
                    ) = save_versioned_upload(
                        settings_result["beat"],
                        "beat",
                    )
                if settings_result["vocal"] is not None:
                    (
                        st.session_state.vocal_path,
                        st.session_state.file_refs["vocal_path"],
                    ) = save_versioned_upload(
                        settings_result["vocal"],
                        "vocal",
                    )
                st.session_state.analysis_mode = settings_result["analysis_mode"]
                if st.session_state.analysis_mode == "stems":
                    st.session_state.file_path = st.session_state.beat_path
                    st.session_state.file_refs["track_path"] = (
                        st.session_state.file_refs.get("beat_path")
                    )
                if new_version_uploaded:
                    st.session_state.analysis_data = None
                persist_current_session()
                previous_track_for_graph = (
                    old_track_path
                    if (
                        st.session_state.analysis_mode == "master"
                        and st.session_state.file_path != old_track_path
                    )
                    else None
                )
                if new_version_uploaded:
                    update_question = (
                        "Я только что загрузил новую версию трека после правок."
                    )
                else:
                    update_question = (
                        "Я обновил настройки. Оцени текущий трек и подскажи, "
                        "что попробовать дальше."
                    )
                if st.session_state.vibe_description:
                    update_question += (
                        f"\nМои мысли о треке: "
                        f"{st.session_state.vibe_description}"
                    )
                elif new_version_uploaded:
                    update_question += (
                        " Оцени, что изменилось и что попробовать дальше."
                    )
                queue_ai_request(
                    update_question,
                    "settings",
                    previous_track_for_graph,
                    new_version_uploaded,
                )
                st.rerun()
        else:
            chat_result = render_chat(
                st.session_state.chat_history,
                st.session_state.genre,
                process_pending_ai_request,
                persist_current_session,
                session_id=st.session_state.session_id,
                user_id=st.session_state.user_id,
            )

            if chat_result and chat_result["action"] == "settings":
                st.session_state.view_mode = "settings"
                st.rerun()

            if chat_result and chat_result["action"] == "send":
                queue_ai_request(chat_result["question"], "chat")
                st.rerun()

            if chat_result and chat_result["action"] == "consolidate":
                queue_ai_request(
                    "Подведи итог тому, что я сегодня узнал.",
                    "consolidation",
                )
                st.rerun()

    with col_graph:
        if analysis_error:
            st.error(analysis_error)
        else:
            reference_delta = (
                get_reference_delta(current_audio)
                if reference_audio is not None
                else ""
            )
            previous_spectrum = None
            if st.session_state.old_spectrum is not None:
                previous_spectrum = {
                    "spectrum": st.session_state.old_spectrum,
                    "frequencies": st.session_state.old_frequencies,
                }

            render_graph(
                current_audio,
                reference_audio,
                previous_spectrum,
                reference_delta,
                current_stem_data,
                st.session_state.genre
                if st.session_state.analysis_mode == "master"
                else None,
                st.session_state.file_path,
                st.session_state.vocal_path
                if st.session_state.analysis_mode == "stems"
                else None,
                readonly_mode=st.session_state.get("readonly_mode", False),
            )
