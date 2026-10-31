import streamlit as st

from utils.aiutils import AIRequestError
from utils.config import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_USER_MESSAGES,
    MESSAGE_LIMIT_MESSAGE,
)
from utils.session_store import get_message_feedbacks, save_message_feedback


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


def _process_pending_request(chat_history, process_pending_request, save_history):
    payload = st.session_state.pendingai_payload
    try:
        answer = process_pending_request(payload)
    except (AIRequestError, ValueError) as error:
        st.session_state.pendingai_error = str(error)
    else:
        chat_history.append({"role": "assistant", "content": answer})
        save_history()
        st.session_state.pendingai_error = None
    finally:
        st.session_state.pendingai_request = False
    st.session_state.pendingai_payload = None
    st.rerun()


def render_chat(
    chat_history,
    genre,
    process_pending_request=None,
    save_history=None,
    track_name="Трек",
    audio_data=None,
    stem_data=None,
    session_id=None,
    user_id=None,
):
    if "pendingai_request" not in st.session_state:
        st.session_state.pendingai_request = False
    if "pendingai_payload" not in st.session_state:
        st.session_state.pendingai_payload = None

    st.subheader(f"💬 ИИ-Наставник (Жанр: {genre})")
    if st.session_state.get("pendingai_error"):
        st.error(st.session_state.pendingai_error)
        st.session_state.pendingai_error = None

    message_limit_reached = sum(
        message["role"] == "user" for message in chat_history
    ) >= MAX_USER_MESSAGES
    if message_limit_reached:
        st.info(MESSAGE_LIMIT_MESSAGE)

    feedback_by_message = (
        get_message_feedbacks(session_id, user_id)
        if session_id and user_id
        else {}
    )
    chat_box = st.container(height=600)
    with chat_box:
        for message_index, message in enumerate(chat_history):
            with st.chat_message(message["role"]):
                st.write(message["content"])
                if (
                    message["role"] == "assistant"
                    and session_id
                    and user_id
                ):
                    feedback_columns = st.columns([1, 1, 8])
                    for column, rating, label, help_text in (
                        (
                            feedback_columns[0],
                            "positive",
                            "👍",
                            "Полезный ответ",
                        ),
                        (
                            feedback_columns[1],
                            "negative",
                            "👎",
                            "Неполезный ответ",
                        ),
                    ):
                        if column.button(
                            label,
                            key=f"feedback_{session_id}_{message_index}_{rating}",
                            help=help_text,
                            type=(
                                "primary"
                                if feedback_by_message.get(message_index) == rating
                                else "secondary"
                            ),
                        ):
                            try:
                                save_message_feedback(
                                    session_id,
                                    user_id,
                                    message_index,
                                    rating,
                                )
                            except ValueError as error:
                                st.error(str(error))
                            else:
                                st.toast("Спасибо за фидбек!")

    report = _build_markdown_report(
        track_name,
        genre,
        audio_data,
        stem_data,
        chat_history,
    )
    st.download_button(
        "📥 Скачать отчет (Markdown)",
        data=report,
        file_name="svedos_report.md",
        mime="text/markdown",
        use_container_width=True,
    )

    if st.session_state.pendingai_request:
        if process_pending_request is None or save_history is None:
            st.error("Для обработки ожидающего запроса не настроен обработчик ИИ.")
            return None
        _process_pending_request(chat_history, process_pending_request, save_history)

    col_input, col_settings = st.columns([9, 1], vertical_alignment="bottom")
    with col_input:
        user_question = st.chat_input(
            "Задай вопрос...",
            max_chars=MAX_CHAT_MESSAGE_CHARS,
            disabled=message_limit_reached,
        )
    with col_settings:
        settings_clicked = st.button(
            "⚙️",
            key="chat_settings_button",
            use_container_width=True,
            disabled=message_limit_reached,
        )

    consolidate_clicked = st.button(
        "📘 Закрепить знания",
        disabled=not chat_history or message_limit_reached,
        use_container_width=True,
    )

    if settings_clicked:
        return {"action": "settings"}
    if consolidate_clicked:
        return {"action": "consolidate"}
    if user_question:
        return {"action": "send", "question": user_question}
    return None
