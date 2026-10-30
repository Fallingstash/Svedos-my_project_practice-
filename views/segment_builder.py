from uuid import uuid4

import streamlit as st


SEGMENT_PRESETS = (
    ("Интро", 15),
    ("Куплет", 30),
    ("Припев", 20),
    ("Бридж", 15),
)
def _recalculate_timeline(segments):
    start = 0
    for segment in segments:
        duration = max(0.1, round(float(segment["duration"]), 1))
        segment["start"] = start
        segment["end"] = round(start + duration, 1)
        start = segment["end"]


def _add_segment(segments, name, duration):
    matching_names = sum(segment["name"].startswith(name) for segment in segments)
    segment_name = name if matching_names == 0 else f"{name} {matching_names + 1}"
    segments.append(
        {
            "id": uuid4().hex,
            "name": segment_name,
            "start": 0.0,
            "end": float(duration),
            "duration": float(duration),
        }
    )
    _recalculate_timeline(segments)


def render_segment_builder(segments, key_prefix):
    for segment in segments:
        segment.setdefault("id", uuid4().hex)
        if "duration" not in segment:
            segment["duration"] = float(segment["end"]) - float(segment["start"])
    _recalculate_timeline(segments)
    st.caption(
        "Добавляй музыкальные части и меняй их длительность. "
        "Начало каждой следующей части сдвигается автоматически."
    )

    changed = False
    for index, segment in enumerate(segments):
        duration = float(segment["duration"])
        duration_key = f"{key_prefix}_segment_duration_{segment['id']}"
        with st.container(border=True):
            name_col, duration_col, delete_col = st.columns([4, 2, 1])
            name_col.markdown(f"**{segment['name']}**")
            new_duration = duration_col.number_input(
                "Длительность (сек.)",
                min_value=0.1,
                value=duration,
                step=0.1,
                format="%.1f",
                key=duration_key,
            )
            segment["duration"] = round(float(new_duration), 1)
            if segment["duration"] != duration:
                changed = True
            if delete_col.button(
                "×",
                key=f"{key_prefix}_segment_delete_{segment['id']}",
                help="Удалить часть",
            ):
                segments.pop(index)
                changed = True

            st.caption(f"{segment['start']:g}–{segment['end']:g} сек.")

    add_columns = st.columns(len(SEGMENT_PRESETS))
    for column, (name, duration) in zip(add_columns, SEGMENT_PRESETS):
        if column.button(
            f"+ {name}",
            key=f"{key_prefix}_add_{name}",
            use_container_width=True,
        ):
            _add_segment(segments, name, duration)
            changed = True

    if changed:
        _recalculate_timeline(segments)
    return changed
