import streamlit as st
import os
import librosa
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks
from openai import OpenAI
from dotenv import load_dotenv
import pyloudnorm as pyln

load_dotenv()
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("API_KEY"))

st.set_page_config(page_title="Svedos", layout="wide")

# --- 1. ИНИЦИАЛИЗАЦИЯ ПАМЯТИ ---
if 'analyzed' not in st.session_state: st.session_state.analyzed = False
if 'analyzing' not in st.session_state: st.session_state.analyzing = False  # Флаг жесткой блокировки
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'old_spectrum' not in st.session_state:
    st.session_state.old_spectrum = None
    st.session_state.old_frequencies = None
if 'segments' not in st.session_state: st.session_state.segments = []
if 'genre' not in st.session_state: st.session_state.genre = "Рэп"
if 'file_path' not in st.session_state: st.session_state.file_path = ""
if 'ref_path' not in st.session_state: st.session_state.ref_path = None
if 'view_mode' not in st.session_state: st.session_state.view_mode = "chat"

st.markdown("""
    <style>
        .block-container { padding-top: 2rem; }
        h1 { margin-bottom: 40px; }
    </style>
""", unsafe_allow_html=True)

st.title("🎧 Svedos")


# --- ФУНКЦИЯ ГЕНЕРАЦИИ ПРОМПТА ---
def get_strict_prompt(question, segments_list, genre_name, request_data=""):
    seg_text = ""
    if segments_list:
        seg_text = "Пользователь выделил части трека: " + ", ".join(
            [f"{s['name']} ({s['start']}-{s['end']} сек)" for s in segments_list])

    system = f"""Ты опытный, но дружелюбный наставник по сведению музыки в жанре {genre_name}. 
    Твой пользователь присылает МАСТЕР-ТРЕК.
    КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать сложные технические термины (Mid/Side, Q-фактор, шелвинг, фундаментал, мультибанд, ratio).
    Объясняй на пальцах, как другу, который впервые открыл FL Studio. 
    Говори простыми словами: "бубнит", "глухо", "резко", "добавь высоких", "убери низы".
    {seg_text}"""

    user = f"Вот данные о пиках мастера: {request_data}. Вопрос пользователя: {question}" if request_data else question
    return system, user


# --- 2. СТАРТОВЫЙ ЭКРАН (Онбординг) ---
if not st.session_state.analyzed:

    # Если мы прямо сейчас анализируем, показываем спиннер
    if st.session_state.analyzing:
        st.info("Идет анализ трека... Пожалуйста, подождите.")
        with st.spinner("Нейросеть слушает ваш трек..."):
            # Анализ
            y, sr = librosa.load(st.session_state.file_path, sr=None)
            D = librosa.stft(y, n_fft=2048)
            D_db = librosa.amplitude_to_db(np.abs(D), ref=np.max)
            average_spectrum = np.mean(D_db, axis=1)
            frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
            peaks, _ = find_peaks(average_spectrum, prominence=5)
            peak_amplitudes = average_spectrum[peaks]
            sorted_indices = np.argsort(peak_amplitudes)[::-1]

            request = ""
            for i in range(3):
                best_idx = sorted_indices[i]
                request += f"{i + 1}. {frequencies[peaks[best_idx]]:.1f} Гц (Громкость: {peak_amplitudes[best_idx]:.1f} дБ)\n"

            sys_prompt, user_prompt = get_strict_prompt("Дай мне 3 конкретных совета по эквализации.",
                                                        st.session_state.segments, st.session_state.genre, request)

            response = client.chat.completions.create(
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}]
            )
            st.session_state.chat_history.append({"role": "assistant", "content": response.choices[0].message.content})

            # ПЕРЕХОДИМ НА РАБОЧИЙ ЭКРАН
            st.session_state.analyzed = True
            st.session_state.analyzing = False
            st.rerun()

    else:
        st.write("Добро пожаловать в Svedos. Загрузите свой трек, чтобы получить советы по сведению от ИИ.")

        with st.form("upload_form"):
            uploaded_file = st.file_uploader("Загрузите трек", type=["mp3", "wav"], accept_multiple_files=False)
            ref_file = st.file_uploader("Загрузите референс (опционально)", type=["mp3", "wav"],
                                        accept_multiple_files=False)
            genre = st.selectbox("Жанр", ["Рэп", "Хип-Хоп", "Поп", "Лоу-фай"])
            analyze_btn = st.form_submit_button("🚀 Начать анализ", type="primary")

        # Сегменты на стартовом экране
        st.subheader("Сегменты трека (опционально)")
        with st.form("start_segment_form", clear_on_submit=True):
            seg_name = st.text_input("Название", "Куплет")
            col1, col2 = st.columns(2)
            start_t = col1.number_input("Начало (сек)", min_value=0, value=10, step=1)
            end_t = col2.number_input("Конец (сек)", min_value=1, value=30, step=1)
            submit_seg = st.form_submit_button("Добавить сегмент")
            if submit_seg:
                st.session_state.segments.append({"name": seg_name, "start": start_t, "end": end_t})

        if st.session_state.segments:
            for i, seg in enumerate(st.session_state.segments):
                c1, c2 = st.columns([4, 1])
                c1.write(f"- {seg['name']}: {seg['start']} - {seg['end']} сек.")
                if c2.button("❌", key=f"start_del_{i}"):
                    st.session_state.segments.pop(i)
                    st.rerun()

        # ЖЕСТКАЯ БЛОКИРОВКА ОТ ДАБЛ-КЛИКА
        if analyze_btn and uploaded_file is not None:
            # СРАЗУ СОХРАНЯЕМ ФАЙЛЫ НА ДИСК
            with open("temp_audio.mp3", "wb") as f:
                f.write(uploaded_file.getbuffer())
            if ref_file is not None:
                with open("temp_ref.mp3", "wb") as f:
                    f.write(ref_file.getbuffer())
                st.session_state.ref_path = "temp_ref.mp3"

            st.session_state.genre = genre
            st.session_state.file_path = "temp_audio.mp3"

            # Включаем режим анализа и перезапускаем
            st.session_state.analyzing = True
            st.rerun()

# --- 3. РАБОЧАЯ ОБЛАСТЬ ---
else:
    with st.sidebar:
        st.header("Мои сессии")
        if st.button("➕ Новая сессия", use_container_width=True):
            st.session_state.analyzed = False
            st.session_state.chat_history = []
            st.session_state.old_spectrum = None
            st.session_state.segments = []
            st.session_state.ref_path = None
            st.session_state.view_mode = "chat"
            st.rerun()
        st.divider()
        st.caption("История треков (в разработке)")

    col_chat, col_graph = st.columns([1, 1])

    # ================= ЛЕВАЯ ЧАСТЬ (ЧАТ ИЛИ НАСТРОЙКИ) =================
    with col_chat:

        # --- РЕЖИМ 1: НАСТРОЙКИ СЕССИИ ---
        if st.session_state.view_mode == "settings":
            c_head, c_close = st.columns([4, 1])
            c_head.subheader("⚙️ Настройки трека")
            if c_close.button("❌", key="close_settings"):
                st.session_state.view_mode = "chat"
                st.rerun()

            # 1. Сначала обновление трека и референса
            st.write("**Обновить трек / Добавить референс:**")
            new_uploaded = st.file_uploader("Новая версия трека", type=["mp3", "wav"], key="new_track")
            new_ref = st.file_uploader("Новый референс", type=["mp3", "wav"], key="new_ref")

            if st.button("Применить изменения", type="primary"):
                if new_uploaded is not None:
                    y_old, sr_old = librosa.load(st.session_state.file_path, sr=None)
                    D_old = librosa.stft(y_old, n_fft=2048)
                    st.session_state.old_spectrum = np.mean(librosa.amplitude_to_db(np.abs(D_old), ref=np.max), axis=1)
                    st.session_state.old_frequencies = librosa.fft_frequencies(sr=sr_old, n_fft=2048)
                    with open("temp_audio_new.mp3", "wb") as f:
                        f.write(new_uploaded.getbuffer())
                    st.session_state.file_path = "temp_audio_new.mp3"

                if new_ref is not None:
                    with open("temp_ref_new.mp3", "wb") as f:
                        f.write(new_ref.getbuffer())
                    st.session_state.ref_path = "temp_ref_new.mp3"

                st.session_state.view_mode = "chat"
                st.rerun()

            st.divider()

            # 2. Затем сегменты (с подписью, что они для текущего трека)
            st.write("**Сегменты текущего трека:**")
            with st.form("segment_form", clear_on_submit=True):
                seg_name = st.text_input("Название", "Куплет")
                c1, c2 = st.columns(2)
                start_t = c1.number_input("Начало (сек)", min_value=0, value=10)
                end_t = c2.number_input("Конец (сек)", min_value=1, value=30)
                if st.form_submit_button("Добавить сегмент"):
                    st.session_state.segments.append({"name": seg_name, "start": start_t, "end": end_t})
                    st.rerun()

            if st.session_state.segments:
                for i, seg in enumerate(st.session_state.segments):
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"- {seg['name']} ({seg['start']}-{seg['end']})")
                    if c2.button("❌", key=f"del_{i}"):
                        st.session_state.segments.pop(i)
                        st.rerun()

        # --- РЕЖИМ 2: ЧАТ ---
        else:
            c_head, c_settings = st.columns([3, 1])
            c_head.subheader(f"💬 ИИ-Наставник (Жанр: {st.session_state.genre})")
            if c_settings.button("⚙️ Настроить", use_container_width=True):
                st.session_state.view_mode = "settings"
                st.rerun()

            chat_box = st.container(height=550)
            with chat_box:
                for msg in st.session_state.chat_history:
                    with st.chat_message(msg["role"]):
                        st.write(msg["content"])

            with st.form("chat_form", clear_on_submit=True):
                user_q = st.text_input("Вопрос...", label_visibility="collapsed")
                send_btn = st.form_submit_button("Отправить", use_container_width=True)
                if send_btn and user_q:
                    st.session_state.chat_history.append({"role": "user", "content": user_q})
                    with st.spinner("Думаю..."):
                        sys_prompt, user_prompt = get_strict_prompt(user_q, st.session_state.segments,
                                                                    st.session_state.genre)
                        response = client.chat.completions.create(
                            model="nvidia/nemotron-3-ultra-550b-a55b:free",
                            messages=[{"role": "system", "content": sys_prompt},
                                      {"role": "user", "content": user_prompt}]
                        )
                        st.session_state.chat_history.append(
                            {"role": "assistant", "content": response.choices[0].message.content})
                    st.rerun()

    # ================= ПРАВАЯ ЧАСТЬ (ГРАФИК) =================
    with col_graph:
        with st.container(height=800):
            st.subheader("📊 Спектр трека")

            y, sr = librosa.load(st.session_state.file_path, sr=None)
            D = librosa.stft(y, n_fft=2048)
            D_db = librosa.amplitude_to_db(np.abs(D), ref=np.max)
            average_spectrum = np.mean(D_db, axis=1)
            frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
            peaks, _ = find_peaks(average_spectrum, prominence=5)
            peak_amplitudes = average_spectrum[peaks]
            sorted_indices = np.argsort(peak_amplitudes)[::-1]

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(frequencies, average_spectrum, color='purple', label="Текущий")

            if st.session_state.ref_path is not None:
                y_ref, sr_ref = librosa.load(st.session_state.ref_path, sr=None)
                D_ref = librosa.stft(y_ref, n_fft=2048)
                D_db_ref = librosa.amplitude_to_db(np.abs(D_ref), ref=np.max)
                ref_spectrum = np.mean(D_db_ref, axis=1)
                ref_frequencies = librosa.fft_frequencies(sr=sr_ref, n_fft=2048)
                ax.plot(ref_frequencies, ref_spectrum, color='green', alpha=0.6, linestyle='-.', label="Референс")

            if st.session_state.old_spectrum is not None:
                ax.plot(st.session_state.old_frequencies, st.session_state.old_spectrum, color='gray', alpha=0.5,
                        linestyle='--', label="Прошлая версия")

            ax.legend()
            ax.set_xscale('log')
            ax.set_xlim(20, 16000)
            ax.grid(True, which="both", ls="--", alpha=0.5)

            for i in range(3):
                best_idx = sorted_indices[i]
                ax.plot(frequencies[peaks[best_idx]], peak_amplitudes[best_idx], 'ro')

            st.pyplot(fig)

            meter = pyln.Meter(sr)
            st.metric("Громкость (LUFS)", f"{meter.integrated_loudness(y):.1f}")
            st.metric("Пиковый уровень (dBFS)", f"{20 * np.log10(np.max(np.abs(y))):.1f}")