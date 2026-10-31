import matplotlib.pyplot as plt
import streamlit as st

from utils.audioutils import get_genre_reference_curve


def _render_metric_grid(metrics, columns=3):
    for offset in range(0, len(metrics), columns):
        metric_columns = st.columns(columns)
        for column, (label, value) in zip(
            metric_columns,
            metrics[offset : offset + columns],
        ):
            column.metric(label, value)


def render_graph(
    audio_data,
    reference_data=None,
    previous_spectrum=None,
    reference_delta="",
    stem_data=None,
    genre_name=None,
    audio_path=None,
    vocal_path=None,
):
    with st.container(height=800):
        st.subheader("📊 Спектр трека")
        if audio_path:
            st.audio(audio_path)
        if vocal_path:
            st.audio(vocal_path)

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
        elif genre_name:
            axis.plot(
                audio_data["frequencies"],
                get_genre_reference_curve(
                    genre_name,
                    audio_data["frequencies"],
                ),
                color="goldenrod",
                alpha=0.9,
                linewidth=1.2,
                linestyle=":",
                label=f"Средний профиль жанра: {genre_name}",
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
                _render_metric_grid(
                    [
                        ("LUFS", f"{stem['loudness']:.1f}"),
                        ("True Peak", f"{stem['true_peak_db']:.1f} dBTP"),
                        (
                            "Фазовая корреляция",
                            f"{stem['phase_correlation']:.2f}",
                        ),
                        (
                            "Спектральный центроид",
                            f"{stem['spectralcentroid']:.0f} Гц",
                        ),
                        ("Стерео-ширина", f"{stem['stereo_width_pct']:.1f}%"),
                        (
                            "PLR",
                            f"{stem['plr']:.1f} дБ"
                            if stem["plr"] is not None
                            else "—",
                        ),
                    ]
                )
            st.caption(
                "Разброс LUFS за 3-секундные окна: "
                f"бит — {_format_variance(stem_data['beat'])}, "
                f"вокал — {_format_variance(stem_data['vocal'])}"
            )
        else:
            _render_metric_grid(
                [
                    ("Громкость (LUFS)", f"{audio_data['loudness']:.1f}"),
                    ("Пиковый уровень (dBFS)", f"{audio_data['max_peak_db']:.1f}"),
                    ("True Peak (dBTP)", f"{audio_data['true_peak_db']:.1f}"),
                    (
                        "Фазовая корреляция",
                        f"{audio_data['phase_correlation']:.2f}",
                    ),
                    (
                        "Спектральный центроид",
                        f"{audio_data['spectralcentroid']:.0f} Гц",
                    ),
                    ("Crest Factor", f"{audio_data['crest_factor']:.1f} дБ"),
                    ("Стерео-ширина", f"{audio_data['stereo_width_pct']:.1f}%"),
                    (
                        "PLR",
                        f"{audio_data['plr']:.1f} дБ"
                        if audio_data["plr"] is not None
                        else "—",
                    ),
                    (
                        "Разброс LUFS (3 сек.)",
                        f"{audio_data['short_term_lufs_variance']:.2f} дБ²"
                        if audio_data["short_term_lufs_variance"] is not None
                        else "Недостаточно длины",
                    ),
                ]
            )

        if audio_data.get("segment_analyses"):
            with st.expander("Метрики по сегментам"):
                for segment in audio_data["segment_analyses"]:
                    st.markdown(
                        f"**{segment['name']} "
                        f"({segment['start']:g}–{segment['end']:g} сек.)**"
                    )
                    segment_sources = (
                        (
                            ("Бит", segment["beat"]),
                            ("Вокал", segment["vocal"]),
                        )
                        if "beat" in segment and "vocal" in segment
                        else (("", segment),)
                    )
                    for source_name, metrics in segment_sources:
                        if source_name:
                            st.caption(source_name)
                        peaks = ", ".join(
                            f"{frequency:.0f} Гц"
                            for frequency, _ in metrics["top_peaks"]
                        ) or "не обнаружены"
                        _render_metric_grid(
                            [
                                ("Топ-3 пика", peaks),
                                ("LUFS", f"{metrics['loudness']:.1f}"),
                                (
                                    "True Peak",
                                    f"{metrics['true_peak_db']:.1f} dBTP",
                                ),
                                (
                                    "Crest Factor",
                                    f"{metrics['crest_factor']:.1f} дБ",
                                ),
                            ]
                        )


def _format_variance(audio_data):
    variance = audio_data["short_term_lufs_variance"]
    return f"{variance:.2f} дБ²" if variance is not None else "недостаточно длины"
