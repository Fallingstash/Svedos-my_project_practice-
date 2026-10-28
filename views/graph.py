import matplotlib.pyplot as plt
import streamlit as st


def render_graph(
    audio_data,
    reference_data=None,
    previous_spectrum=None,
    reference_delta="",
    stem_data=None,
):
    with st.container(height=800):
        st.subheader("📊 Спектр трека")

        figure, axis = plt.subplots(figsize=(8, 4))
        axis.plot(
            audio_data["frequencies"],
            audio_data["spectrum"],
            color="purple",
            label="Бит" if stem_data else "Текущий",
        )

        if stem_data:
            axis.plot(
                stem_data["vocal"]["frequencies"],
                stem_data["vocal"]["spectrum"],
                color="orange",
                alpha=0.8,
                label="Вокал",
            )

        if reference_data is not None:
            axis.plot(
                reference_data["frequencies"],
                reference_data["spectrum"],
                color="green",
                alpha=0.6,
                linestyle="-.",
                label="Референс",
            )

        if previous_spectrum is not None:
            axis.plot(
                previous_spectrum["frequencies"],
                previous_spectrum["spectrum"],
                color="gray",
                alpha=0.5,
                linestyle="--",
                label="Прошлая версия",
            )

        axis.legend()
        axis.set_xscale("log")
        axis.set_xlim(20, 16000)
        axis.grid(True, which="both", ls="--", alpha=0.5)

        for frequency, db in audio_data["top_peaks"]:
            axis.plot(frequency, db, "ro")

        st.pyplot(figure)
        plt.close(figure)

        if reference_delta:
            with st.expander("Сравнение с референсом"):
                st.write(reference_delta)

        if stem_data:
            st.subheader("Метрики стемов")
            for label, stem in (
                ("Бит", stem_data["beat"]),
                ("Вокал", stem_data["vocal"]),
            ):
                st.markdown(f"**{label}**")
                col_lufs, col_peak, col_width, col_plr = st.columns(4)
                col_lufs.metric("LUFS", f"{stem['loudness']:.1f}")
                col_peak.metric("True Peak", f"{stem['true_peak_db']:.1f} dBTP")
                col_width.metric(
                    "Стерео-ширина",
                    f"{stem['stereo_width_pct']:.1f}%",
                )
                col_plr.metric(
                    "PLR",
                    f"{stem['plr']:.1f} дБ" if stem["plr"] is not None else "—",
                )
            st.caption(
                "Разброс LUFS за 3-секундные окна: "
                f"бит — {_format_variance(stem_data['beat'])}, "
                f"вокал — {_format_variance(stem_data['vocal'])}"
            )
        else:
            st.metric("Громкость (LUFS)", f"{audio_data['loudness']:.1f}")
            st.metric("Пиковый уровень (dBFS)", f"{audio_data['max_peak_db']:.1f}")
            st.metric("True Peak (dBTP)", f"{audio_data['true_peak_db']:.1f}")
            st.metric("Crest Factor", f"{audio_data['crest_factor']:.1f} дБ")
            st.metric("Стерео-ширина", f"{audio_data['stereo_width_pct']:.1f}%")
            if audio_data["plr"] is not None:
                st.metric("PLR", f"{audio_data['plr']:.1f} дБ")
            variance = audio_data["short_term_lufs_variance"]
            st.metric(
                "Разброс LUFS (3 сек.)",
                f"{variance:.2f} дБ²"
                if variance is not None
                else "Недостаточно длины",
            )


def _format_variance(audio_data):
    variance = audio_data["short_term_lufs_variance"]
    return f"{variance:.2f} дБ²" if variance is not None else "недостаточно длины"
