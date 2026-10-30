import streamlit as st

from utils.aiutils import AIRequestError


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
):
    if "pendingai_request" not in st.session_state:
        st.session_state.pendingai_request = False
    if "pendingai_payload" not in st.session_state:
        st.session_state.pendingai_payload = None

    st.subheader(f"💬 ИИ-Наставник (Жанр: {genre})")
    if st.session_state.get("pendingai_error"):
        st.error(st.session_state.pendingai_error)
        st.session_state.pendingai_error = None

    chat_box = st.container(height=600)
    with chat_box:
        for message in chat_history:
            with st.chat_message(message["role"]):
                st.write(message["content"])

    if st.session_state.pendingai_request:
        if process_pending_request is None or save_history is None:
            st.error("Для обработки ожидающего запроса не настроен обработчик ИИ.")
            return None
        _process_pending_request(chat_history, process_pending_request, save_history)

    col_input, col_settings = st.columns([9, 1], vertical_alignment="bottom")
    with col_input:
        user_question = st.chat_input("Задай вопрос...")
    with col_settings:
        settings_clicked = st.button(
            "⚙️",
            key="chat_settings_button",
            use_container_width=True,
        )

    consolidate_clicked = st.button(
        "📘 Закрепить знания",
        disabled=not chat_history,
        use_container_width=True,
    )

    if settings_clicked:
        return {"action": "settings"}
    if consolidate_clicked:
        return {"action": "consolidate"}
    if user_question:
        return {"action": "send", "question": user_question}
    return None
