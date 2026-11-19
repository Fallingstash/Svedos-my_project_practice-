import streamlit as st

from utils.aiutils import AIRequestError
from utils.config import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_USER_MESSAGES,
    MESSAGE_LIMIT_MESSAGE,
    get_message_limit,
)
from utils.session_store import get_message_feedbacks, save_message_feedback


def _process_pending_request(chat_history, process_pending_request, save_history):
    payload = st.session_state.pendingai_payload
    try:
        with st.spinner("ИИ анализирует..."):
            answer = process_pending_request(payload)
    except (AIRequestError, ValueError) as error:
        if payload.get("kind") != "consolidation":
            user_message = {
                "role": "user",
                "content": payload.get("question", ""),
            }
            if chat_history and chat_history[-1] == user_message:
                chat_history.pop()
                save_history()
        st.session_state.pendingai_error = str(error)
    else:
        if payload.get("kind") != "consolidation":
            chat_history.append(
                {"role": "user", "content": payload.get("question", "")}
            )
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
    session_id=None,
    user_id=None,
):
    if "pendingai_request" not in st.session_state:
        st.session_state.pendingai_request = False
    if "pendingai_payload" not in st.session_state:
        st.session_state.pendingai_payload = None

    st.subheader(f"💬 ИИ-Наставник (Жанр: {genre})")
    used_messages = sum(
        message["role"] == "user" for message in chat_history
    )
    message_limit = get_message_limit()
    remaining_messages = max(0, message_limit - used_messages)
    remaining_percent = round(remaining_messages / message_limit * 100)
    message_limit_reached = remaining_messages == 0
    consolidate_clicked = st.button(
        "📘 Закрепить знания",
        disabled=(
            not chat_history
            or message_limit_reached
            or st.session_state.pendingai_request
        ),
        use_container_width=True,
    )

    if st.session_state.get("pendingai_error"):
        st.error(st.session_state.pendingai_error)
        st.session_state.pendingai_error = None

    if message_limit_reached:
        st.warning(
            f"{MESSAGE_LIMIT_MESSAGE} "
            "Использовано 10 из 10 сообщений этой сессии. "
            "Новая платная версия пока недоступна; оплата не подключена."
        )

    feedback_by_message = (
        get_message_feedbacks(session_id, user_id)
        if session_id and user_id
        else {}
    )
    chat_box = st.container(height=600)
    with chat_box:
        for message_index, message in enumerate(chat_history):
            if message["role"] == "system":
                st.info(message["content"])
                continue
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

    if st.session_state.pendingai_request:
        if process_pending_request is None or save_history is None:
            st.error("Для обработки ожидающего запроса не настроен обработчик ИИ.")
            return None
        _process_pending_request(chat_history, process_pending_request, save_history)

    st.progress(
        remaining_percent / 100,
        text=f"Использовано {used_messages} из {message_limit} сообщений "
        "(бесплатный лимит)",
    )
    attached_metrics = st.session_state.setdefault("attachedmetrics", [])
    if attached_metrics:
        attached_columns = st.columns(min(len(attached_metrics), 4))
        for index, metric_name in enumerate(tuple(attached_metrics)):
            if attached_columns[index % len(attached_columns)].button(
                f"❌ Убрать {metric_name}",
                key=f"attached_metric_remove_{session_id or 'new'}_{index}",
                help=f"Открепить метрику «{metric_name}»",
                use_container_width=True,
            ):
                attached_metrics.remove(metric_name)
                st.rerun()

    user_question = st.chat_input(
        "Задай вопрос...",
        max_chars=MAX_CHAT_MESSAGE_CHARS,
        disabled=message_limit_reached,
    )

    settings_clicked = st.button(
        "⚙️ Настройки трека",
        key="chat_settings_button",
        use_container_width=True,
    )

    if settings_clicked:
        return {"action": "settings"}
    if consolidate_clicked:
        return {"action": "consolidate"}
    if user_question:
        return {"action": "send", "question": user_question}
    return None
