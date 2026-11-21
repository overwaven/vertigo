"""Enhance — upscale images and remove backgrounds."""

from __future__ import annotations

import streamlit as st

from vertigo import config, fal, i18n, ui
from vertigo.empty_state import empty_state
from vertigo.jobs import SAVE_IMAGE

library = ui.current_library()
manager = ui.current_manager()

REMOVE_BG = "remove_bg"
UPSCALE = "upscale"


def _fal_url(asset) -> str | None:
    try:
        return fal.ensure_remote_url(asset, library)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        st.error(i18n.t("error.prepare_asset", name=asset.name, error=exc))
        return None


def _queue_remove_bg(asset) -> None:
    url = _fal_url(asset)
    if not url:
        return
    job_id = manager.submit(
        kind="remove_bg",
        label=i18n.t("jobs.label.remove_bg", name=asset.name),
        endpoint=config.REMOVE_BG_ENDPOINT,
        arguments={"image_url": url},
        save_kind=SAVE_IMAGE,
        parent_id=asset.id,
    )
    st.session_state.enhance_jobs.insert(0, job_id)
    st.toast(i18n.t("toast.bg_queued"), icon=":material/queue:")


def _queue_upscale(asset, model: dict, arguments: dict) -> None:
    url = _fal_url(asset)
    if not url:
        return
    job_id = manager.submit(
        kind="upscale",
        label=i18n.t("jobs.label.upscale", model=i18n.t(f"models.{model['id']}.label"), name=asset.name),
        endpoint=model["endpoint"],
        arguments={**arguments, "image_url": url},
        save_kind=SAVE_IMAGE,
        parent_id=asset.id,
    )
    st.session_state.enhance_jobs.insert(0, job_id)
    st.toast(i18n.t("toast.upscale_queued"), icon=":material/queue:")


ui.key_ready()

remove_label = i18n.t("enhance.mode_remove_bg")
upscale_label = i18n.t("enhance.mode_upscale")
mode = st.segmented_control(
    "enhance_mode",
    [REMOVE_BG, UPSCALE],
    default=REMOVE_BG,
    format_func=lambda value: remove_label if value == REMOVE_BG else upscale_label,
    key="enhance_mode",
    label_visibility="collapsed",
)

left, right = st.columns([2, 3], gap="large")

with left:
    with st.container(border=True):
        if mode == UPSCALE:
            asset = ui.image_picker(
                library, key="upscale_source", label=i18n.t("enhance.image")
            )
            model_map = {
                i18n.t(f"models.{m['id']}.label"): m["id"]
                for m in config.UPSCALE_MODELS
            }
            labels = list(model_map)
            label = ui.icon_selectbox(
                i18n.t("studio.model"),
                labels,
                param_type="upscale_model",
                value_map=model_map,
                key="upscale_model",
            )
            model = next(
                m
                for m in config.UPSCALE_MODELS
                if i18n.t(f"models.{m['id']}.label") == label
            )
            arguments: dict = {}
            if model["engine"] == "topaz":
                with st.expander(
                    i18n.t("studio.parameters"), type="compact", expanded=True
                ):
                    arguments["model"] = ui.icon_selectbox(
                        i18n.t("enhance.enhancer"),
                        config.TOPAZ_MODELS,
                        param_type="topaz_model",
                        key="upscale_topaz_model",
                    )
                    arguments["upscale_factor"] = st.slider(
                        i18n.t("enhance.factor"),
                        1.0,
                        4.0,
                        2.0,
                        0.5,
                        key="upscale_factor",
                    )
                    arguments["output_format"] = ui.icon_selectbox(
                        i18n.t("param.format"),
                        ["jpeg", "png"],
                        param_type="output_format",
                        key="upscale_format",
                    )
                    arguments["face_enhancement"] = st.toggle(
                        i18n.t("enhance.face"), value=True, key="upscale_face"
                    )
            if st.button(
                i18n.t("enhance.upscale"),
                type="primary",
                icon=":material/high_quality:",
                disabled=not (fal.ready() and asset),
                key="upscale_go",
                width="stretch",
            ):
                if asset is not None:
                    _queue_upscale(asset, model, arguments)
        else:
            asset = ui.image_picker(
                library, key="rmbg_source", label=i18n.t("enhance.image")
            )
            if st.button(
                i18n.t("enhance.remove_bg"),
                type="primary",
                icon=":material/content_cut:",
                disabled=not (fal.ready() and asset),
                key="rmbg_go",
                width="stretch",
            ):
                if asset is not None:
                    _queue_remove_bg(asset)

with right:
    if st.session_state.enhance_jobs:
        ui.jobs_panel(st.session_state.enhance_jobs)
    else:
        empty_state(
            icon="high_quality",
            title=i18n.t("enhance.stage.title"),
            description=i18n.t("enhance.stage.body"),
        )
