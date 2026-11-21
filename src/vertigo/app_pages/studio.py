"""Image studio — text-to-image, reference-guided editing and vectorisation."""

from __future__ import annotations

from typing import Any

import streamlit as st

from vertigo import config, fal, i18n, ui
from vertigo.empty_state import empty_state
from vertigo.jobs import SAVE_IMAGE, SAVE_IMAGES, Job
from vertigo.reference_tray import reference_tray
from vertigo.store import Asset

library = ui.current_library()
manager = ui.current_manager()

VISUAL_KINDS = [config.KIND_IMAGE, config.KIND_VECTOR]
TAB_GENERATE = "generate"
TAB_VECTOR = "vector"


# --------------------------------------------------------------------------- #
# References
# --------------------------------------------------------------------------- #


def _reference_tray() -> list[str]:
    refs: list[str] = list(st.session_state.studio_refs)

    upload_column, library_column = st.columns(
        2, gap="small", vertical_alignment="bottom"
    )
    with upload_column:
        uploaded = st.file_uploader(
            i18n.t("studio.refs.add"),
            type=config.IMAGE_UPLOAD_TYPES,
            accept_multiple_files=True,
            key="studio_ref_uploader",
            help=i18n.t("studio.refs.help"),
        )
    with library_column:
        with st.popover(
            i18n.t("studio.refs.from_library"),
            icon=":material/photo_library:",
            width="stretch",
        ):
            candidates = [
                asset
                for asset in library.newest_first(library.filter(kinds=[config.KIND_IMAGE]))
                if asset.id not in refs
            ][:60]
            if candidates:
                picked = ui.thumbnail_table(
                    library,
                    candidates,
                    key="studio_ref_table",
                    height=240,
                    selection_mode="multi-row",
                )
                if st.button(i18n.t("studio.refs.add_selected"), key="studio_ref_add"):
                    for asset in picked:
                        refs.append(asset.id)
                    st.session_state.studio_refs = refs
                    st.rerun()
            else:
                st.caption(i18n.t("studio.refs.none_library"))

    for asset_id in ui.ingest_uploads(
        uploaded, index_key="studio_upload_index", origin=config.ORIGIN_REFERENCE
    ):
        if asset_id not in refs:
            refs.append(asset_id)

    refs = [r for r in refs if library.get(r)]
    st.session_state.studio_refs = refs

    if refs:
        items = []
        for asset_id in refs:
            asset = library.get(asset_id)
            if asset is None:
                continue
            src = ui.thumbnail_data_url(asset, library, size=192)
            if src:
                items.append({"id": asset.id, "name": asset.name, "src": src})
        if items:
            removed_id, reordered_ids = reference_tray(
                items,
                remove_label=i18n.t("studio.refs.remove"),
                key="studio_reference_tray",
            )
            if removed_id and removed_id in refs:
                st.session_state.studio_refs = [r for r in refs if r != removed_id]
                st.rerun()
            if (
                reordered_ids
                and len(reordered_ids) == len(refs)
                and set(reordered_ids) == set(refs)
                and reordered_ids != refs
            ):
                st.session_state.studio_refs = reordered_ids
                st.rerun()
    return list(st.session_state.studio_refs)


# --------------------------------------------------------------------------- #
# Generate & edit
# --------------------------------------------------------------------------- #


def _build_image_request(
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    urls: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the fal endpoint and payload for an image generation or edit."""
    urls = list(urls or [])
    arguments: dict[str, Any] = {
        "prompt": prompt.strip(),
        "num_images": int(params["num_images"]),
    }
    engine = model["engine"]
    if engine == "openai":
        arguments.update(
            image_size=params["image_size"],
            quality=params["quality"],
            reasoning_effort=params["reasoning_effort"],
            background=params["background"],
            output_format=params["output_format"],
        )
        if urls:
            arguments["image_urls"] = urls
            return model["edit"], arguments
        return model["t2i"], arguments

    if engine == "ideogram":
        arguments.update(
            image_size=params["image_size"],
            quality=params["quality"],
        )
        if urls:
            arguments["image_url"] = urls[0]
            if len(urls) > 1:
                arguments["reference_image_urls"] = urls[1:]
            arguments["edit_precision"] = params["edit_precision"]
            if params["background"] == "transparent":
                arguments["transparent_background"] = True
            return model["edit"], arguments
        arguments["transparent_background"] = params["background"] == "transparent"
        return model["t2i"], arguments

    arguments.update(
        aspect_ratio=params["aspect_ratio"],
        resolution=params["resolution"],
        thinking_level=params["thinking_level"],
        safety_tolerance=params["safety_tolerance"],
        output_format=params["output_format"],
    )
    if params.get("enable_web_search"):
        arguments["enable_web_search"] = True
    if urls:
        arguments["image_urls"] = urls
        return model["edit"], arguments
    return model["t2i"], arguments


def _image_parameters(model: dict[str, Any]) -> dict[str, Any]:
    """Compact parameter grid (two columns) with value-aware icons."""
    params: dict[str, Any] = {}
    engine = model["engine"]
    size_map = dict(config.GPT_IMAGE_SIZES)
    size_labels = [label for label, _ in config.GPT_IMAGE_SIZES]

    if engine == "openai":
        left, right = st.columns(2, gap="small")
        with left:
            size_label = ui.icon_selectbox(
                i18n.t("param.image_size"),
                size_labels,
                param_type="image_size",
                value_map=size_map,
                index=0,
                key=f"studio_size_{model['id']}",
            )
            params["image_size"] = size_map[size_label]
            params["quality"] = ui.icon_selectbox(
                i18n.t("param.quality"),
                config.GPT_QUALITIES,
                param_type="quality",
                index=config.GPT_QUALITIES.index("high"),
                key=f"studio_quality_{model['id']}",
            )
            params["reasoning_effort"] = ui.icon_selectbox(
                i18n.t("param.reasoning"),
                config.GPT_REASONING_EFFORTS,
                param_type="reasoning_effort",
                index=config.GPT_REASONING_EFFORTS.index("high"),
                key=f"studio_reasoning_{model['id']}",
            )
        with right:
            params["background"] = ui.icon_selectbox(
                i18n.t("param.background"),
                config.GPT_BACKGROUNDS,
                param_type="background",
                index=0,
                key=f"studio_bg_{model['id']}",
            )
            params["output_format"] = ui.icon_selectbox(
                i18n.t("param.format"),
                config.IMAGE_OUTPUT_FORMATS,
                param_type="output_format",
                index=0,
                key=f"studio_fmt_{model['id']}",
            )
    elif engine == "ideogram":
        left, right = st.columns(2, gap="small")
        with left:
            size_label = ui.icon_selectbox(
                i18n.t("param.image_size"),
                size_labels,
                param_type="image_size",
                value_map=size_map,
                index=0,
                key=f"studio_size_{model['id']}",
            )
            params["image_size"] = size_map[size_label]
            params["quality"] = ui.icon_selectbox(
                i18n.t("param.quality"),
                config.IDEOGRAM_QUALITIES,
                param_type="quality",
                index=config.IDEOGRAM_QUALITIES.index("medium"),
                key=f"studio_quality_{model['id']}",
            )
        with right:
            params["background"] = ui.icon_selectbox(
                i18n.t("param.background"),
                config.IDEOGRAM_BACKGROUNDS,
                param_type="background",
                index=0,
                key=f"studio_bg_{model['id']}",
            )
            params["edit_precision"] = ui.icon_selectbox(
                i18n.t("param.edit_precision"),
                config.IDEOGRAM_EDIT_PRECISIONS,
                param_type="edit_precision",
                index=0,
                key=f"studio_precision_{model['id']}",
            )
    else:
        left, right = st.columns(2, gap="small")
        with left:
            params["aspect_ratio"] = ui.icon_selectbox(
                i18n.t("param.aspect_ratio"),
                config.NANO_ASPECT_RATIOS,
                param_type="aspect_ratio",
                index=0,
                key=f"studio_ar_{model['id']}",
            )
            params["resolution"] = ui.icon_selectbox(
                i18n.t("param.resolution"),
                config.NANO_RESOLUTIONS,
                param_type="resolution",
                index=config.NANO_RESOLUTIONS.index("1K"),
                key=f"studio_res_{model['id']}",
            )
            params["thinking_level"] = ui.icon_selectbox(
                i18n.t("param.thinking"),
                config.NANO_THINKING_LEVELS,
                param_type="thinking_level",
                index=config.NANO_THINKING_LEVELS.index("medium"),
                key=f"studio_thinking_{model['id']}",
            )
        with right:
            params["safety_tolerance"] = ui.icon_selectbox(
                i18n.t("param.safety"),
                config.SAFETY_TOLERANCES,
                param_type="safety_tolerance",
                index=config.SAFETY_TOLERANCES.index("4"),
                key=f"studio_safety_{model['id']}",
            )
            params["output_format"] = ui.icon_selectbox(
                i18n.t("param.format"),
                config.IMAGE_OUTPUT_FORMATS,
                param_type="output_format",
                index=0,
                key=f"studio_fmt_{model['id']}",
            )
            params["enable_web_search"] = st.toggle(
                i18n.t("param.web_search"),
                value=False,
                key=f"studio_web_{model['id']}",
            )
    params["num_images"] = st.slider(
        i18n.t("param.images"),
        min_value=1,
        max_value=min(4, int(model["max_images"])),
        value=1,
        key=f"studio_num_{model['id']}",
    )
    return params


def _resolve_references(refs: list[str]) -> tuple[list[str], str | None]:
    urls: list[str] = []
    for asset_id in refs:
        asset = library.get(asset_id)
        if asset is None:
            continue
        try:
            urls.append(fal.ensure_remote_url(asset, library))
        except Exception as exc:  # noqa: BLE001 - surface to the user
            st.error(i18n.t("error.prepare_asset", name=asset.name, error=exc))
            return [], None
    return urls, (refs[0] if urls else None)


def _queue_image(model: dict[str, Any], prompt: str, refs: list[str], params: dict[str, Any]) -> None:
    max_refs = int(model["max_refs"])
    if len(refs) > max_refs:
        st.toast(
            i18n.t("toast.max_refs", model=model["label"], count=max_refs),
            icon=":material/info:",
        )
        refs = refs[:max_refs]
    urls, parent_id = _resolve_references(refs)
    if refs and not urls:
        return  # reference preparation failed
    endpoint, arguments = _build_image_request(model, prompt, params, urls)
    if urls:
        label = i18n.t("jobs.label.edit", model=model["label"])
    else:
        label = i18n.t("jobs.label.generation", model=model["label"])

    job_id = manager.submit(
        kind="image",
        label=label,
        endpoint=endpoint,
        arguments=arguments,
        save_kind=SAVE_IMAGES,
        parent_id=parent_id,
    )
    st.session_state.studio_jobs.insert(0, job_id)
    st.toast(i18n.t("toast.queued"), icon=":material/queue:")


def _studio_asset_actions(asset: Asset, job: Job) -> None:
    if asset.kind not in VISUAL_KINDS:
        return
    if st.button(
        i18n.t("studio.edit_reuse"),
        key=f"studio_edit_{job.id}_{asset.id}",
        icon=":material/edit_note:",
        help=i18n.t("studio.edit_reuse_help"),
        width="stretch",
    ):
        current = [r for r in st.session_state.studio_refs if r != asset.id]
        st.session_state.studio_refs = [asset.id, *current]
        st.toast(i18n.t("toast.base_added"), icon=":material/edit_note:")
        st.rerun()


def _render_generate() -> None:
    left, right = st.columns([2, 3], gap="large")

    with left:
        with st.container(border=True):
            model_id = st.pills(
                i18n.t("studio.model"),
                [m["id"] for m in config.IMAGE_MODELS],
                default=config.IMAGE_MODELS[0]["id"],
                format_func=lambda value: (
                    f"{config.model_by_id(value)['icon']} "
                    f"{config.model_by_id(value)['label']}"
                ),
                key="studio_model",
                required=True,
                help="\n\n".join(
                    i18n.t(f"models.{m['id']}.tagline") for m in config.IMAGE_MODELS
                ),
            )
            model = config.model_by_id(model_id or config.IMAGE_MODELS[0]["id"])

            prompt = st.text_area(
                i18n.t("studio.prompt"),
                height=100,
                key="studio_prompt",
                placeholder=i18n.t("studio.prompt_placeholder"),
            )
            refs = _reference_tray()

            ready = fal.ready() and bool(prompt.strip())
            generate = st.button(
                i18n.t("studio.generate"),
                type="primary",
                icon=":material/auto_awesome:",
                disabled=not ready,
                key="studio_generate",
                width="stretch",
            )

            with st.expander(
                i18n.t("studio.parameters"), type="compact", expanded=False
            ):
                params = _image_parameters(model)

            if generate:
                _queue_image(model, prompt, refs, params)

    with right:
        if st.session_state.studio_jobs:
            ui.jobs_panel(
                st.session_state.studio_jobs,
                on_asset=_studio_asset_actions,
            )
        else:
            empty_state(
                icon="imagesmode",
                title=i18n.t("studio.stage.generate_title"),
                description=i18n.t("studio.stage.generate_body"),
            )


# --------------------------------------------------------------------------- #
# Vectors
# --------------------------------------------------------------------------- #


def _render_vectors() -> None:
    mode = st.segmented_control(
        i18n.t("studio.vector.mode"),
        ["generate", "trace"],
        default="generate",
        format_func=lambda value: i18n.t(f"studio.vector.{value}_mode"),
        key="studio_vector_mode",
    )
    left, right = st.columns([2, 3], gap="large")

    with left:
        with st.container(border=True):
            if mode == "trace":
                asset = ui.image_picker(
                    library, key="studio_trace_source", label=i18n.t("studio.vector.image_to_trace")
                )
                if st.button(
                    i18n.t("studio.vector.trace"),
                    type="primary",
                    icon=":material/polyline:",
                    disabled=not (fal.ready() and asset),
                    key="studio_trace_go",
                    width="stretch",
                ):
                    if asset is not None:
                        try:
                            url = fal.ensure_remote_url(asset, library)
                        except Exception as exc:  # noqa: BLE001
                            st.error(i18n.t("error.prepare_asset", name=asset.name, error=exc))
                        else:
                            job_id = manager.submit(
                                kind="vector",
                                label=i18n.t("jobs.label.vectorize", name=asset.name),
                                endpoint=config.VECTOR_ENDPOINT_TRACE,
                                arguments={"image_url": url},
                                save_kind=SAVE_IMAGE,
                                parent_id=asset.id,
                            )
                            st.session_state.studio_jobs.insert(0, job_id)
                            st.toast(i18n.t("toast.tracing"), icon=":material/queue:")
            else:
                prompt = st.text_area(
                    i18n.t("studio.prompt"),
                    height=100,
                    key="studio_vector_prompt",
                    placeholder=i18n.t("studio.prompt_placeholder"),
                )
                style_column, size_column = st.columns(2, gap="small")
                style_map = dict(config.VECTOR_STYLES)
                with style_column:
                    style_labels = [label for label, _ in config.VECTOR_STYLES]
                    style_label = ui.icon_selectbox(
                        i18n.t("studio.vector.style"),
                        style_labels,
                        param_type="vector_style",
                        value_map=style_map,
                        key="studio_vector_style",
                    )
                with size_column:
                    size_labels = list(config.VECTOR_SIZE_MAP)
                    size_label = ui.icon_selectbox(
                        i18n.t("studio.vector.size"),
                        size_labels,
                        param_type="image_size",
                        value_map=config.VECTOR_SIZE_MAP,
                        index=size_labels.index("Square (1024²)"),
                        key="studio_vector_size",
                    )
                if st.button(
                    i18n.t("studio.vector.generate"),
                    type="primary",
                    icon=":material/polyline:",
                    disabled=not (fal.ready() and prompt.strip()),
                    key="studio_vector_go",
                    width="stretch",
                ):
                    job_id = manager.submit(
                        kind="vector",
                        label=i18n.t("jobs.label.vector"),
                        endpoint=config.VECTOR_ENDPOINT_T2V,
                        arguments={
                            "prompt": prompt.strip(),
                            "style": dict(config.VECTOR_STYLES)[style_label],
                            "image_size": config.VECTOR_SIZE_MAP[size_label],
                        },
                        save_kind=SAVE_IMAGES,
                    )
                    st.session_state.studio_jobs.insert(0, job_id)
                    st.toast(i18n.t("toast.queued_vector"), icon=":material/queue:")

    with right:
        if st.session_state.studio_jobs:
            ui.jobs_panel(
                st.session_state.studio_jobs,
                title=i18n.t("studio.vector.jobs"),
                on_asset=_studio_asset_actions,
            )
        else:
            empty_state(
                icon="polyline",
                title=i18n.t("studio.stage.vector_title"),
                description=i18n.t("studio.stage.vector_body"),
            )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

ui.key_ready()

mode = st.segmented_control(
    "studio_mode",
    [TAB_GENERATE, TAB_VECTOR],
    default=TAB_GENERATE,
    format_func=lambda value: i18n.t(f"studio.mode_{value}"),
    key="studio_mode",
    label_visibility="collapsed",
)

if mode == TAB_VECTOR:
    _render_vectors()
else:
    _render_generate()
