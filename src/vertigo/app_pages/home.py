"""Home page — an at-a-glance overview of the library and quick entry points."""

from __future__ import annotations

import streamlit as st

from vertigo import config, fal, i18n, ui

library = ui.current_library()
counts = library.counts()
queue = ui.current_manager().counts()

if not fal.ready():
    st.warning(
        i18n.t("home.no_key", url=config.FAL_DASHBOARD_URL),
        icon=":material/key:",
    )
    st.page_link(
        "app_pages/settings.py",
        label=i18n.t("home.add_key"),
        icon=":material/settings:",
    )

with st.grid(4, gap="small"):
    st.metric(
        i18n.t("home.metric.library"),
        counts["total"],
        border=True,
        height="stretch",
    )
    st.metric(
        i18n.t("home.metric.visual"),
        counts[config.KIND_IMAGE] + counts[config.KIND_VECTOR],
        border=True,
        height="stretch",
    )
    st.metric(
        i18n.t("home.metric.speech"),
        counts[config.KIND_AUDIO] + counts[config.KIND_TEXT],
        border=True,
        height="stretch",
    )
    st.metric(
        i18n.t("home.metric.jobs"),
        queue["active"],
        border=True,
        height="stretch",
    )

st.subheader(i18n.t("home.start"), icon=":material/bolt:")

_card_content = [
    (
        ":material/auto_awesome:",
        i18n.t("home.card.studio.title"),
        i18n.t("home.card.studio.body"),
        "app_pages/studio.py",
    ),
    (
        ":material/high_quality:",
        i18n.t("home.card.enhance.title"),
        i18n.t("home.card.enhance.body"),
        "app_pages/enhance.py",
    ),
    (
        ":material/record_voice_over:",
        i18n.t("home.card.speech.title"),
        i18n.t("home.card.speech.body"),
        "app_pages/speech.py",
    ),
]
cards_grid = st.grid(3, gap="small", border=True)
for icon, title, body, path in _card_content:
    with cards_grid.cell():
        st.subheader(title, icon=icon)
        st.markdown(body)
        st.space("stretch")
        st.page_link(
            path,
            label=i18n.t("home.open"),
            icon=":material/arrow_forward:",
        )

st.subheader(i18n.t("home.recent_visuals"), icon=":material/imagesmode:")
recent_visuals = library.recent(8, kinds=[config.KIND_IMAGE, config.KIND_VECTOR])
if recent_visuals:
    ui.asset_grid(recent_visuals, library, columns=4)
else:
    st.caption(
        i18n.t("home.recent_visuals_empty", path="app_pages/studio.py")
    )

audio_and_text = library.recent(6, kinds=[config.KIND_AUDIO, config.KIND_TEXT])
if audio_and_text:
    st.subheader(i18n.t("home.recent_speech"), icon=":material/graphic_eq:")
    for asset in audio_and_text:
        with st.container(border=True):
            if asset.kind == config.KIND_AUDIO:
                st.audio(
                    library.bytes_of(asset),
                    format=asset.media_root or "audio/mpeg",
                    alt=asset.name,
                )
                st.caption(f"{asset.name} · {ui.relative_time(asset.created_at)}")
            else:
                st.markdown(f"{config.KIND_ICONS[asset.kind]} **{asset.name}**")
                st.caption(ui.relative_time(asset.created_at))
                text = library.text_of(asset)
                st.text(text[:280] + ("…" if len(text) > 280 else ""))
