from datetime import datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import streamlit as st

from utils.aiutils import (
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
from utils.session_store import save_session
from views.chat import render_chat
from views.graph import render_graph
from views.onboarding import render_onboarding
from views.settings import render_settings
from views.settings import MAX_UPLOAD_SIZE_BYTES
from views.sidebar import render_sidebar


st.set_page_config(page_title="Svedos", layout="wide")

SESSION_DEFAULTS = {
    "analyzed": False,
    "chat_history": [],
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
    "pendingai_request": False,
    "pendingai_payload": None,
    "pendingai_error": None,
    "last_ai_request_at": None,
    "chat_upload_generation": 0,
    "renaming_session_id": None,
}
for key, default in SESSION_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = (
            default.copy() if isinstance(default, list) else default
        )
if not st.session_state.user_id:
    st.session_state.user_id = str(uuid4())

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


def save_versioned_upload(uploaded_file, prefix):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in {".mp3", ".wav"}:
        raise ValueError("Поддерживаются только файлы MP3 и WAV.")
    file_buffer = uploaded_file.getbuffer()
    if file_buffer.nbytes > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("Максимальный размер аудиофайла — 30 МБ.")

    if not st.session_state.session_id:
        st.session_state.session_id = str(uuid4())
        st.session_state.session_created_at = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
    session_directory = (
        Path(__file__).resolve().parent
        / "history"
        / st.session_state.session_id
    )
    session_directory.mkdir(parents=True, exist_ok=True)

    version = 1
    while (session_directory / f"{prefix}_v{version}{extension}").exists():
        version += 1
    file_path = session_directory / f"{prefix}_v{version}{extension}"
    with file_path.open("wb") as audio_file:
        audio_file.write(file_buffer)
    return str(file_path)


def persist_current_session():
    if not st.session_state.session_id or not st.session_state.file_path:
        return
    save_session(
        {
            "id": st.session_state.session_id,
            "user_id": st.session_state.user_id,
            "track_name": st.session_state.track_name,
            "display_name": st.session_state.display_name or st.session_state.track_name,
            "genre": st.session_state.genre,
            "vibe_description": st.session_state.vibe_description,
            "created_at": st.session_state.session_created_at,
            "track_path": st.session_state.file_path,
            "reference_path": st.session_state.ref_path,
            "analysis_mode": st.session_state.analysis_mode,
            "beat_path": st.session_state.beat_path,
            "vocal_path": st.session_state.vocal_path,
            "segments": st.session_state.segments,
            "chat_history": st.session_state.chat_history,
        }
    )


def get_reference_data():
    reference_path = st.session_state.ref_path
    if not reference_path:
        return None
    if not Path(reference_path).is_file():
        raise ValueError("Файл референса из этой сессии не найден.")
    return analyze_audio_file(reference_path)


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
    return request_ai_response(system_prompt, user_prompt, chat_history)


def process_pending_ai_request(payload):
    if payload["kind"] == "consolidation":
        system_prompt, user_prompt = get_consolidation_prompt(
            st.session_state.genre
        )
        with st.status("ИИ думает...", expanded=False) as status:
            answer = request_ai_response(
                system_prompt,
                user_prompt,
                st.session_state.chat_history[:-1],
            )
            status.update(label="Ответ готов", state="complete", expanded=False)
        return answer

    with st.status("Слушаю трек...", expanded=True) as status:
        previous_track_path = payload.get("previous_track_path")
        if previous_track_path:
            previous_audio = analyze_audio_file(
                previous_track_path,
                analysis_segments(),
            )
            st.session_state.old_spectrum = previous_audio["spectrum"]
            st.session_state.old_frequencies = previous_audio["frequencies"]

        status.update(label="Считаю частоты...", state="running")
        audio_data, stem_data = analyze_current_source()
        reference_delta = get_reference_delta(audio_data)
        status.update(label="ИИ думает...", state="running")
        answer = request_analysis(
            payload["question"],
            audio_data,
            st.session_state.chat_history[:-1],
            reference_delta,
            stem_data,
            payload.get("new_version", False),
        )
        status.update(label="Ответ готов", state="complete", expanded=False)
        return answer


def queue_ai_request(
    question,
    kind,
    previous_track_path=None,
    new_version=False,
):
    now = monotonic()
    last_request_at = st.session_state.last_ai_request_at
    if last_request_at is not None and now - last_request_at < 5:
        st.session_state.pendingai_error = "Не так быстро!"
        return False

    st.session_state.last_ai_request_at = now
    st.session_state.chat_history.append({"role": "user", "content": question})
    st.session_state.pendingai_payload = {
        "kind": kind,
        "question": question,
        "previous_track_path": previous_track_path,
        "new_version": new_version,
    }
    st.session_state.pendingai_request = True
    st.session_state.view_mode = "chat"
    persist_current_session()
    return True


render_sidebar()


if not st.session_state.analyzed:
    onboarding_result = render_onboarding(st.session_state.segments)
    if onboarding_result:
        st.session_state.session_id = str(uuid4())
        st.session_state.session_created_at = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        st.session_state.genre = onboarding_result["genre"]
        st.session_state.vibe_description = onboarding_result.get(
            "vibe_description",
            "",
        )
        st.session_state.session_vibe_input = st.session_state.vibe_description
        st.session_state.chat_history = []
        st.session_state.old_spectrum = None
        st.session_state.old_frequencies = None
        st.session_state.analysis_mode = onboarding_result.get(
            "analysis_mode",
            st.session_state.get("onboarding_analysis_mode", "master"),
        )
        if st.session_state.analysis_mode == "master":
            track = onboarding_result["track"]
            st.session_state.track_name = track.name
            st.session_state.display_name = track.name
            st.session_state.file_path = save_versioned_upload(track, "track")
            st.session_state.beat_path = None
            st.session_state.vocal_path = None
            reference = onboarding_result["reference"]
            st.session_state.ref_path = (
                save_versioned_upload(reference, "reference")
                if reference is not None
                else None
            )
            initial_question = "Дай мне 3 конкретных совета по сведению этого трека."
        else:
            beat = onboarding_result["beat"]
            vocal = onboarding_result["vocal"]
            st.session_state.beat_path = save_versioned_upload(beat, "beat")
            st.session_state.vocal_path = save_versioned_upload(vocal, "vocal")
            st.session_state.file_path = st.session_state.beat_path
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
    col_chat, col_graph = st.columns([1, 1])

    with col_chat:
        if st.session_state.view_mode == "settings":
            settings_result = render_settings(
                st.session_state.segments,
                st.session_state.vibe_description,
            )

            if settings_result and settings_result["action"] == "segments_updated":
                persist_current_session()
                st.rerun()

            if settings_result and settings_result["action"] == "close":
                st.session_state.view_mode = "chat"
                st.rerun()

            if settings_result and settings_result["action"] == "apply":
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
                    st.session_state.file_path = save_versioned_upload(
                        settings_result["track"],
                        "track",
                    )

                if settings_result["reference"] is not None:
                    st.session_state.ref_path = save_versioned_upload(
                        settings_result["reference"],
                        "reference",
                    )

                if settings_result["beat"] is not None:
                    st.session_state.beat_path = save_versioned_upload(
                        settings_result["beat"],
                        "beat",
                    )
                if settings_result["vocal"] is not None:
                    st.session_state.vocal_path = save_versioned_upload(
                        settings_result["vocal"],
                        "vocal",
                    )
                st.session_state.analysis_mode = settings_result["analysis_mode"]
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
                if st.session_state.analysis_mode == "stems":
                    if not st.session_state.beat_path or not st.session_state.vocal_path:
                        st.error("Для режима стемов загрузи и бит, и вокал.")
                    else:
                        queue_ai_request(
                            update_question,
                            "settings",
                            previous_track_for_graph,
                            new_version_uploaded,
                        )
                        st.rerun()
                else:
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
        try:
            current_audio, stem_data = analyze_current_source()
            reference_audio = get_reference_data()
        except ValueError as error:
            st.error(str(error))
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
                stem_data,
                st.session_state.genre
                if st.session_state.analysis_mode == "master"
                else None,
            )
