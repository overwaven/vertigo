"""Speech — ElevenLabs Eleven v4, Gemini 3.8 Flash TTS and Scribe transcription."""

from __future__ import annotations

from typing import Any

import streamlit as st

from vertigo import config, fal, i18n, naming, ui
from vertigo.empty_state import empty_state
from vertigo.jobs import SAVE_AUDIO, SAVE_TRANSCRIPT

library = ui.current_library()
manager = ui.current_manager()

TTS = "tts"
STT = "stt"


def _build_tts_request(
    model: dict[str, Any], text: str, params: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Build the fal endpoint and payload for a text-to-speech job."""
    clean_text = text.strip()
    if model["engine"] == "gemini":
        arguments: dict[str, Any] = {
            "prompt": clean_text,
            "voice": params["voice"],
        }
        style_instructions = str(params.get("style_instructions") or "").strip()
        if style_instructions:
            arguments["style_instructions"] = style_instructions
        language = str(params.get("language_code") or "").strip()
        if language:
            arguments["language_code"] = language
        return model["endpoint"], arguments

    arguments = {
        "text": clean_text,
        "voice": params["voice"],
        "stability": params["stability"],
        "similarity_boost": params["similarity_boost"],
        "apply_text_normalization": params["apply_text_normalization"],
        "output_format": params["output_format"],
    }
    language = str(params.get("language_code") or "").strip()
    if language:
        arguments["language_code"] = language
    return model["endpoint"], arguments


def _tts_parameters(model: dict[str, Any]) -> dict[str, Any]:
    """Render model-specific delivery parameters with value-aware icons."""
    params: dict[str, Any] = {}
    if model["engine"] == "gemini":
        params["style_instructions"] = st.text_input(
            i18n.t("speech.style_instructions"),
            placeholder=i18n.t("speech.style_instructions_placeholder"),
            key=f"tts_style_instructions_{model['id']}",
            icon=":material/tune:",
        )
        params["language_code"] = st.text_input(
            i18n.t("speech.language"),
            placeholder="en-US, ru-RU…",
            key=f"tts_language_{model['id']}",
            icon=":material/translate:",
        )
        return params

    params["stability"] = st.slider(
        i18n.t("speech.stability"),
        0.0,
        1.0,
        0.5,
        0.05,
        key=f"tts_stability_{model['id']}",
    )
    params["similarity_boost"] = st.slider(
        i18n.t("speech.similarity"),
        0.0,
        1.0,
        0.75,
        0.05,
        key=f"tts_similarity_{model['id']}",
    )
    col_norm, col_fmt = st.columns(2, gap="small")
    with col_norm:
        params["apply_text_normalization"] = ui.icon_selectbox(
            i18n.t("speech.normalization"),
            config.TEXT_NORMALIZATION,
            param_type="text_normalization",
            index=0,
            key=f"tts_normalization_{model['id']}",
        )
    with col_fmt:
        params["output_format"] = ui.icon_selectbox(
            i18n.t("speech.output_format"),
            config.ELEVEN_OUTPUT_FORMATS,
            param_type="audio_format",
            index=0,
            key=f"tts_format_{model['id']}",
        )
    params["language_code"] = st.text_input(
        i18n.t("speech.language"),
        placeholder="en, ru, es, fr…",
        key=f"tts_language_{model['id']}",
        icon=":material/translate:",
    )
    return params


def _queue_tts(model: dict[str, Any], text: str, params: dict[str, Any]) -> None:
    endpoint, arguments = _build_tts_request(model, text, params)
    job_id = manager.submit(
        kind="tts",
        label=i18n.t(
            "jobs.label.tts", model=model["label"], voice=params.get("voice", "")
        ),
        endpoint=endpoint,
        arguments=arguments,
        save_kind=SAVE_AUDIO,
        name_hint=naming.slugify(text.strip()),
    )
    st.session_state.speech_jobs.insert(0, job_id)
    st.toast(i18n.t("toast.speech_queued"), icon=":material/queue:")


def _queue_transcription(asset, arguments: dict[str, Any]) -> None:
    try:
        url = fal.ensure_remote_url(asset, library)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        st.error(i18n.t("error.prepare_asset", name=asset.name, error=exc))
        return
    job_id = manager.submit(
        kind="stt",
        label=i18n.t("jobs.label.stt", name=asset.name),
        endpoint=config.STT_ENDPOINT,
        arguments={**arguments, "audio_url": url},
        save_kind=SAVE_TRANSCRIPT,
        parent_id=asset.id,
    )
    st.session_state.speech_jobs.insert(0, job_id)
    st.toast(i18n.t("toast.transcribe_queued"), icon=":material/queue:")


ui.key_ready()

tts_label = i18n.t("speech.mode_tts")
stt_label = i18n.t("speech.mode_stt")
mode = st.segmented_control(
    i18n.t("speech.mode_label"),
    [TTS, STT],
    default=TTS,
    required=True,
    format_func=lambda value: tts_label if value == TTS else stt_label,
    key="speech_mode",
    label_visibility="collapsed",
    persist_state="session",
)

left, right = st.columns([2, 3], gap="large")

with left:
    with st.container(border=True):
        if mode == STT:
            asset = ui.audio_picker(
                library, key="stt_source", label=i18n.t("speech.audio")
            )
            language = st.text_input(
                i18n.t("speech.language"),
                placeholder="eng, spa, fra…",
                key="stt_language",
                icon=":material/translate:",
            )
            diarize = st.toggle(i18n.t("speech.diarize"), value=True, key="stt_diarize")
            tag_events = st.toggle(
                i18n.t("speech.events"), value=True, key="stt_events"
            )
            keyterms_raw = st.text_input(
                i18n.t("speech.keyterms"),
                key="stt_keyterms",
                help=i18n.t("speech.keyterms_help"),
            )

            arguments: dict[str, Any] = {
                "diarize": diarize,
                "tag_audio_events": tag_events,
            }
            if language.strip():
                arguments["language_code"] = language.strip()
            keyterms = [
                term.strip() for term in keyterms_raw.split(",") if term.strip()
            ]
            if keyterms:
                arguments["keyterms"] = keyterms

            if st.button(
                i18n.t("speech.transcribe"),
                type="primary",
                icon=":material/speech_to_text:",
                disabled=not (fal.ready() and asset),
                key="stt_go",
                width="stretch",
            ):
                if asset is not None:
                    _queue_transcription(asset, arguments)
        else:
            model_id = st.pills(
                i18n.t("studio.model"),
                [m["id"] for m in config.TTS_MODELS],
                default=config.TTS_MODELS[0]["id"],
                format_func=lambda value: (
                    f"{config.tts_model_by_id(value)['icon']} "
                    f"{config.tts_model_by_id(value)['label']}"
                ),
                key="tts_model",
                required=True,
                persist_state="session",
                help="\n\n".join(
                    i18n.t(f"models.{m['id']}.tagline") for m in config.TTS_MODELS
                ),
            )
            model = config.tts_model_by_id(model_id or config.TTS_MODELS[0]["id"])

            text = st.text_area(
                i18n.t("speech.text"),
                height=160,
                key="tts_text",
                placeholder=i18n.t("speech.text_placeholder"),
            )
            voices = (
                config.GEMINI_TTS_VOICES
                if model["engine"] == "gemini"
                else config.TTS_VOICES
            )
            voice = ui.icon_selectbox(
                i18n.t("speech.voice"),
                voices,
                param_type="voice",
                index=0,
                key=f"tts_voice_{model['id']}",
            )

            generate = st.button(
                i18n.t("speech.generate"),
                type="primary",
                icon=":material/text_to_speech:",
                disabled=not (fal.ready() and text.strip()),
                key="tts_go",
                width="stretch",
            )

            with st.expander(
                i18n.t("studio.parameters"), type="compact", expanded=False
            ):
                params = _tts_parameters(model)
            params["voice"] = voice

            if generate:
                _queue_tts(model, text, params)

with right:
    if st.session_state.speech_jobs:
        ui.jobs_panel(st.session_state.speech_jobs)
    else:
        empty_state(
            icon="record_voice_over",
            title=i18n.t("speech.stage.title"),
            description=i18n.t("speech.stage.body"),
        )
