"""Shared Streamlit UI helpers used across the Vertigo pages."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import streamlit as st

from . import auth, config, fal, i18n
from .jobs import Job, JobManager, get_manager, status_color, status_icon
from .store import Asset, Library, get_library


# --------------------------------------------------------------------------- #
# Cookie bridge
# --------------------------------------------------------------------------- #

# `st.html` content is not iframed, so a small script runs on the app origin
# where it can write `document.cookie`. Values are read back through
# `st.context.cookies` on the next full page load.
_COOKIE_SCRIPT = """
(function () {
  const items = %s;
  for (const p of items) {
    if (p.value === null || p.value === undefined || p.value === "") {
      document.cookie = p.name + "=; path=/; max-age=0; SameSite=Lax";
    } else {
      document.cookie =
        p.name + "=" + encodeURIComponent(p.value) +
        "; path=/; max-age=" + (p.maxAge || 0) + "; SameSite=Lax";
    }
  }
})();
"""


def read_cookie(name: str) -> str | None:
    """Read a cookie from the initial request."""
    try:
        value = st.context.cookies.get(name)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def queue_cookie(name: str, value: str, max_age: int = 0) -> None:
    pending = st.session_state.setdefault("_cookie_queue", [])
    pending.append({"name": name, "value": value, "maxAge": max_age})


def cookie_token() -> str | None:
    """Read the remember-me token from the initial request's cookies."""
    return read_cookie(auth.COOKIE_NAME)


def remember_session(user_id: str) -> None:
    """Queue a remember-me cookie for the signed-in profile."""
    token = auth.create_session(user_id)
    # Keep the token in session state too: the cookie is only readable from the
    # initial request, not from subsequent WebSocket reruns.
    st.session_state["_session_token"] = token
    queue_cookie(auth.COOKIE_NAME, token, auth.SESSION_DAYS * 86_400)


def forget_session_cookie() -> None:
    """Revoke the current token server-side and queue a cookie clear."""
    token = st.session_state.pop("_session_token", None) or cookie_token()
    if token:
        auth.revoke_session(token)
    queue_cookie(auth.COOKIE_NAME, "", 0)


def render_cookie_bridge() -> None:
    """Apply queued cookie writes, if any (fire-and-forget script)."""
    pending = st.session_state.pop("_cookie_queue", None)
    if not pending:
        return
    encoded = json.dumps(pending).replace("</", "<\\/")
    st.html(
        f"<script>{_COOKIE_SCRIPT % encoded}</script>",
        unsafe_allow_javascript=True,
    )


# --------------------------------------------------------------------------- #
# Language
# --------------------------------------------------------------------------- #


def current_language() -> str:
    """Resolve the interface language: explicit choice → profile → cookie → browser."""
    chosen = st.session_state.get("lang")
    if chosen:
        return i18n.normalize(chosen)

    user_id = st.session_state.get("user_id")
    if user_id:
        user = auth.get_user(user_id)
        if user and user.locale:
            return i18n.normalize(user.locale)

    from_cookie = read_cookie(i18n.LANG_COOKIE)
    if from_cookie:
        return i18n.normalize(from_cookie)

    try:
        return i18n.normalize(st.context.locale)
    except Exception:
        return i18n.DEFAULT_LANGUAGE


def set_language(code: str) -> None:
    """Switch language, persist it to the profile and cookie, and rerun."""
    clean = i18n.normalize(code)
    st.session_state["lang"] = clean
    i18n.set_language(clean)
    user_id = st.session_state.get("user_id")
    if user_id:
        auth.set_locale(user_id, clean)
    queue_cookie(i18n.LANG_COOKIE, clean, 365 * 86_400)
    st.rerun()


def language_switch(label: str | None = None) -> None:
    """Compact language picker. The widget key changes with the language so it
    always reflects the resolved value without stale session state."""
    current = i18n.get_language()
    choice = st.segmented_control(
        label or i18n.t("language.label"),
        options=list(i18n.LANGUAGES),
        default=current,
        required=True,
        format_func=i18n.language_label,
        key=f"lang_switch_{current}",
        label_visibility="collapsed" if label == "" else "visible",
    )
    if choice and choice != current:
        set_language(choice)


# --------------------------------------------------------------------------- #
# Profile scope
# --------------------------------------------------------------------------- #


def current_user_id() -> str:
    """Return the signed-in profile id, stopping the page if there is none."""
    user_id = st.session_state.get("user_id")
    if not user_id:
        st.error(i18n.t("error.not_signed_in"), icon=":material/lock:")
        st.stop()
    return user_id


def current_library() -> Library:
    """The media library belonging to the signed-in profile."""
    return get_library(current_user_id())


def current_manager() -> JobManager:
    """The generation queue belonging to the signed-in profile."""
    return get_manager(current_user_id())


_PROFILE_MEDIA_KEYS = ("studio_refs", "studio_jobs", "enhance_jobs", "speech_jobs")
_PROFILE_UPLOAD_KEYS = (
    "studio_upload_index",
    "studio_trace_source_index",
    "rmbg_source_index",
    "upscale_source_index",
    "stt_source_index",
)
_PROFILE_TRANSIENT_KEYS = (
    "delete_asset_ids",
    "wipe_assets",
    "remove_user_id",
    "_clear_library_selection",
    "library_table",
    "lib_page",
)


def reset_profile_session() -> None:
    """Drop per-profile selections so nothing leaks across a profile switch."""
    for key in _PROFILE_MEDIA_KEYS:
        st.session_state[key] = []
    for key in _PROFILE_UPLOAD_KEYS:
        st.session_state[key] = {}
    for key in _PROFILE_TRANSIENT_KEYS:
        st.session_state.pop(key, None)


# --------------------------------------------------------------------------- #
# Parameter icons & selectboxes
# --------------------------------------------------------------------------- #


def param_icon(param_type: str, value: Any) -> str:
    """Return a Material icon reflecting both the parameter type and its value."""
    val = str(value or "").strip().lower()

    if param_type == "image_size":
        return {
            "auto": ":material/aspect_ratio:",
            "square_hd": ":material/crop_square:",
            "square": ":material/crop_square:",
            "landscape_4_3": ":material/crop_landscape:",
            "landscape_16_9": ":material/crop_16_9:",
            "portrait_4_3": ":material/crop_portrait:",
            "portrait_16_9": ":material/stay_current_portrait:",
        }.get(val, ":material/aspect_ratio:")

    if param_type == "aspect_ratio":
        if val == "auto":
            return ":material/aspect_ratio:"
        if val == "1:1":
            return ":material/crop_square:"
        if val == "16:9":
            return ":material/crop_16_9:"
        if val == "3:2":
            return ":material/crop_3_2:"
        if val in ("4:3", "5:4"):
            return ":material/crop_landscape:"
        if val in ("21:9", "4:1", "8:1"):
            return ":material/panorama_horizontal:"
        if val in ("3:4", "4:5", "2:3"):
            return ":material/crop_portrait:"
        if val in ("9:16", "1:4", "1:8"):
            return ":material/panorama_vertical:"
        return ":material/aspect_ratio:"

    if param_type == "quality":
        return {
            "auto": ":material/auto_awesome:",
            "very_low": ":material/bolt:",
            "low": ":material/speed:",
            "medium": ":material/tune:",
            "high": ":material/high_quality:",
            "xhigh": ":material/workspace_premium:",
            "max": ":material/diamond:",
        }.get(val, ":material/high_quality:")

    if param_type == "background":
        return {
            "auto": ":material/auto_awesome:",
            "transparent": ":material/grid_on:",
            "opaque": ":material/layers:",
        }.get(val, ":material/wallpaper:")

    if param_type == "resolution":
        return {
            "0.5k": ":material/sd:",
            "1k": ":material/hd:",
            "2k": ":material/2k:",
            "4k": ":material/4k:",
        }.get(val, ":material/hd:")

    if param_type in ("reasoning_effort", "thinking_level"):
        return {
            "minimal": ":material/bolt:",
            "low": ":material/bolt:",
            "medium": ":material/psychology:",
            "high": ":material/neurology:",
        }.get(val, ":material/psychology:")

    if param_type == "edit_precision":
        return {
            "regular": ":material/brush:",
            "high": ":material/center_focus_strong:",
        }.get(val, ":material/brush:")

    if param_type == "safety_tolerance":
        if val in ("1", "2"):
            return ":material/verified_user:"
        if val in ("5", "6"):
            return ":material/gpp_maybe:"
        return ":material/shield:"

    if param_type == "output_format":
        return {
            "png": ":material/image:",
            "jpeg": ":material/photo:",
            "jpg": ":material/photo:",
            "webp": ":material/photo_library:",
        }.get(val, ":material/image:")

    if param_type == "audio_format":
        if val.startswith("pcm"):
            return ":material/graphic_eq:"
        if val.startswith("opus"):
            return ":material/music_note:"
        return ":material/audio_file:"

    if param_type == "voice":
        return ":material/record_voice_over:"

    if param_type == "text_normalization":
        return {
            "auto": ":material/auto_fix_high:",
            "on": ":material/spellcheck:",
            "off": ":material/format_clear:",
        }.get(val, ":material/auto_fix_high:")

    if param_type == "vector_style":
        if "pixel" in val or "mosaic" in val:
            return ":material/grid_view:"
        if "line" in val or "hand_drawn" in val or "engraving" in val or "thin" in val:
            return ":material/draw:"
        if "pop" in val or "color" in val or "vivid" in val:
            return ":material/palette:"
        if "photo" in val:
            return ":material/photo:"
        return ":material/polyline:"

    if param_type == "upscale_model":
        if "topaz" in val:
            return ":material/tune:"
        return ":material/auto_awesome:"

    if param_type == "topaz_model":
        if "text" in val:
            return ":material/text_fields:"
        if "cgi" in val:
            return ":material/view_in_ar:"
        if "recovery" in val:
            return ":material/healing:"
        if "high fidelity" in val or "max" in val:
            return ":material/high_quality:"
        return ":material/auto_fix_high:"

    return ":material/tune:"


def icon_selectbox(
    label: str,
    options: list[Any],
    *,
    param_type: str,
    key: str,
    index: int = 0,
    format_func: Any = str,
    help: str | None = None,
    value_map: dict[Any, Any] | None = None,
) -> Any:
    """Render a selectbox with a Material icon reflecting its type and value."""
    current = st.session_state.get(key)
    if current not in options:
        current = options[index]
    icon_value = value_map.get(current, current) if value_map else current
    return st.selectbox(
        label,
        options,
        index=index,
        format_func=format_func,
        key=key,
        help=help,
        icon=param_icon(param_type, icon_value),
    )


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def humanize_bytes(size: int) -> str:
    if not size:
        return "—"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def relative_time(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 45:
        return i18n.t("time.just_now")
    if seconds < 3600:
        return i18n.t("time.min_ago", n=int(seconds // 60))
    if seconds < 86400:
        return i18n.t("time.hours_ago", n=int(seconds // 3600))
    if seconds < 604800:
        return i18n.t("time.days_ago", n=int(seconds // 86400))
    return moment.strftime(i18n.t("time.date_format"))


def srt_from_words(words: Iterable[dict[str, Any]]) -> str:
    """Build a SubRip subtitle file from word timings when available."""

    def stamp(seconds: float) -> str:
        millis = int(round((seconds % 1) * 1000))
        total = int(seconds)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d},{millis:03d}"

    lines: list[str] = []
    current: list[str] = []
    start: float | None = None
    last_end = 0.0

    def flush(index: int) -> None:
        nonlocal current, start
        if current and start is not None:
            text = " ".join(current).strip()
            if text:
                lines.append(f"{index}\n{stamp(start)} --> {stamp(last_end)}\n{text}\n")
        current = []
        start = None

    index = 1
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text") or "")
        if word.get("type") == "spacing" or not text.strip():
            continue
        if start is None:
            start = float(word.get("start") or 0)
        current.append(text.strip())
        last_end = float(word.get("end") or last_end)
        if len(" ".join(current)) >= 42 or text.rstrip().endswith((".", "!", "?")):
            flush(index)
            index += 1
    flush(index)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# API key state
# --------------------------------------------------------------------------- #


def key_ready(*, inline: bool = True) -> bool:
    """Return True when generations can run (Demo Mode or fal key)."""
    if fal.ready():
        return True
    if not inline:
        return False
    st.warning(
        i18n.t("error.no_key_inline", url=config.FAL_DASHBOARD_URL),
        icon=":material/key:",
    )
    try:
        st.page_link(
            "app_pages/settings.py",
            label=i18n.t("common.open_settings"),
            icon=":material/settings:",
        )
    except Exception:  # pragma: no cover - navigation fallback
        st.caption(i18n.t("common.open_settings"))
    return False


# --------------------------------------------------------------------------- #
# Asset rendering
# --------------------------------------------------------------------------- #


THUMB_SIZE = 96


@st.cache_data(show_spinner=False, max_entries=1024)
def _thumbnail_data_url(
    path: str, is_svg: bool, file_size: int, max_dim: int = THUMB_SIZE
) -> str | None:
    """Small inline preview for a visual asset.

    ``ImageColumn`` and the reference tray need a URL or data URL (local paths
    aren't supported), so images are downscaled with Pillow and base64-encoded.
    Cached by path, file size and target dimension.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None

    if is_svg:
        return "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")

    try:
        from PIL import Image
    except Exception:  # pragma: no cover - Pillow ships with Streamlit
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((max_dim, max_dim))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=82, method=4)
    except Exception:  # noqa: BLE001 - an unreadable image shouldn't break the page
        return None
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def thumbnail_data_url(
    asset: Asset, library: Library, *, size: int = THUMB_SIZE
) -> str | None:
    """A data-URL thumbnail for a visual asset, or None."""
    if not asset.is_visual:
        return None
    return _thumbnail_data_url(
        str(library.file_path(asset)), asset.is_svg, asset.size, size
    )


def _asset_alt(asset: Asset) -> str:
    """Short plain-text accessible name for a visual or audio asset."""
    prompt = " ".join((asset.prompt or "").split())
    if prompt:
        return prompt[:120] + ("…" if len(prompt) > 120 else "")
    return asset.name or asset.id


def thumbnail_table(
    library: Library,
    assets: list[Asset],
    *,
    key: str,
    height: int = 240,
    selection_mode: str = "multi-row",
    selection_default: dict[str, Any] | None = None,
    alt: str | None = None,
) -> list[Asset]:
    """A selectable table of visual assets with inline thumbnails.

    Returns the selected assets, mapped from stable row positions.
    """
    rows = [
        {
            "Preview": thumbnail_data_url(asset, library),
            "Name": asset.name,
            "Added": relative_time(asset.created_at),
        }
        for asset in assets
    ]
    state = st.dataframe(
        rows,
        key=key,
        hide_index=True,
        height=height,
        on_select="rerun",
        selection_mode=selection_mode,
        selection_default=selection_default,
        column_order=("Preview", "Name", "Added"),
        column_config={
            "Preview": st.column_config.ImageColumn("", width="small"),
            "Name": st.column_config.TextColumn(
                i18n.t("library.column.name"), width="medium"
            ),
            "Added": st.column_config.TextColumn(
                i18n.t("library.column.created"), width="small"
            ),
        },
        placeholder="—",
        alt=alt or i18n.t("common.picker_alt"),
    )
    positions = list(state.selection.rows) if state is not None else []
    return [assets[index] for index in positions if 0 <= index < len(assets)]


def asset_image(asset: Asset, library: Library, *, width: "int | str" = "stretch") -> None:
    """Render a visual asset, handling SVG and raster images alike."""
    if asset.kind == config.KIND_TEXT:
        st.text(library.text_of(asset)[:6000])
        return
    path = library.file_path(asset)
    if not path or not path.exists():
        st.caption(i18n.t("error.missing_file"))
        return
    alt = _asset_alt(asset)
    if asset.is_svg:
        try:
            st.image(path.read_text(encoding="utf-8"), width=width, alt=alt)
        except (OSError, UnicodeDecodeError):
            st.caption(i18n.t("error.render_svg"))
    else:
        st.image(str(path), width=width, alt=alt)


def asset_media(asset: Asset, library: Library) -> bytes:
    return library.bytes_of(asset)


def download_asset_button(
    asset: Asset,
    library: Library,
    *,
    key: str,
    label: str | None = None,
    type: str = "secondary",
    width: "int | str" = "content",
) -> None:
    data = library.bytes_of(asset)
    if not data:
        return
    name = asset.name
    if asset.is_svg and not name.lower().endswith(".svg"):
        name = f"{name}.svg"
    st.download_button(
        label or i18n.t("common.download"),
        data=data,
        file_name=name,
        mime=asset.media_type or "application/octet-stream",
        key=key,
        icon=":material/download:",
        type=type,
        width=width,
    )


def asset_caption(asset: Asset) -> str:
    bits = [relative_time(asset.created_at)]
    if asset.meta.get("width") and asset.meta.get("height"):
        bits.append(f"{asset.meta['width']}×{asset.meta['height']}")
    elif asset.size:
        bits.append(humanize_bytes(asset.size))
    return " · ".join(bits)


def asset_grid(
    assets: list[Asset],
    library: Library,
    *,
    columns: int = 3,
    empty_message: str | None = None,
) -> None:
    if not assets:
        st.caption(empty_message or i18n.t("common.asset_empty"))
        return
    grid = st.grid(columns, gap="small", border=True)
    for asset in assets:
        with grid.cell():
            asset_image(asset, library)
            st.caption(asset.name)
            st.caption(asset_caption(asset))


# --------------------------------------------------------------------------- #
# Uploads & pickers
# --------------------------------------------------------------------------- #


def ingest_uploads(
    uploaded: Any, *, index_key: str, origin: str, kind: str = config.KIND_IMAGE
) -> list[str]:
    """Persist uploaded files, de-duplicating them across reruns."""
    processed: dict[str, str] = st.session_state.setdefault(index_key, {})
    library = current_library()
    if uploaded is None:
        files: list[Any] = []
    elif isinstance(uploaded, list):
        files = uploaded
    else:
        files = [uploaded]

    new_ids: list[str] = []
    for file in files:
        data = file.getvalue()
        digest = hashlib.sha1(data).hexdigest()
        existing = processed.get(digest)
        if existing and library.get(existing):
            continue
        asset = library.add_file(
            data,
            kind=kind,
            name=file.name,
            origin=origin,
            media_type=getattr(file, "type", "") or "application/octet-stream",
            meta={"uploaded": True},
        )
        processed[digest] = asset.id
        new_ids.append(asset.id)
    return new_ids


def _library_choices(library: Library, kinds: Iterable[str]) -> dict[str, str]:
    assets = library.newest_first(library.filter(kinds=kinds))
    return {f"{a.name} · {relative_time(a.created_at)}": a.id for a in assets}


def image_picker(library: Library, *, key: str, label: str | None = None, kinds=None) -> Asset | None:
    """A single-image picker combining an uploader and a thumbnail table."""
    kinds = kinds or [config.KIND_IMAGE]
    # Older versions stored a selectbox value here; reset incompatible state.
    if key in st.session_state and not isinstance(st.session_state[key], dict):
        st.session_state.pop(key, None)

    uploaded = st.file_uploader(
        label or i18n.t("common.source_image"),
        type=config.IMAGE_UPLOAD_TYPES,
        key=f"{key}_upload",
    )
    new_ids = ingest_uploads(
        uploaded, index_key=f"{key}_index", origin=config.ORIGIN_REFERENCE
    )
    assets = library.newest_first(library.filter(kinds=kinds))[:60]
    if not assets:
        st.caption(i18n.t("common.upload_image_hint"))
        return None

    if new_ids:
        index = next((i for i, a in enumerate(assets) if a.id == new_ids[0]), None)
        if index is not None:
            st.session_state[key] = {"selection": {"rows": [index]}}

    selected = thumbnail_table(
        library,
        assets,
        key=key,
        height=200,
        selection_mode="single-row",
        selection_default={"selection": {"rows": [0]}},
    )
    return selected[0] if selected else None


def audio_picker(library: Library, *, key: str, label: str | None = None) -> Asset | None:
    """A single-audio picker combining an uploader and the library."""
    uploaded = st.file_uploader(
        label or i18n.t("speech.audio"),
        type=config.AUDIO_UPLOAD_TYPES,
        key=f"{key}_upload",
    )
    new_ids = ingest_uploads(
        uploaded,
        index_key=f"{key}_index",
        origin=config.ORIGIN_UPLOADED,
        kind=config.KIND_AUDIO,
    )
    options = _library_choices(library, [config.KIND_AUDIO])
    if not options:
        st.caption(i18n.t("common.upload_audio_hint"))
        return None
    if new_ids and new_ids[0] in options.values():
        st.session_state[key] = next(k for k, v in options.items() if v == new_ids[0])
    choice = st.selectbox(i18n.t("common.choose_from_library"), list(options), key=key)
    return library.get(options[choice])


def transcript_srt(asset: Asset) -> str | None:
    words = asset.meta.get("words")
    if not words:
        return None
    return srt_from_words(words) or None


# --------------------------------------------------------------------------- #
# Job rendering
# --------------------------------------------------------------------------- #


_JOB_PREVIEW_CSS = """
<style>
[class*="st-key-job_preview_single_"] img {
    max-width: min(100%, 360px) !important;
    max-height: 340px !important;
    width: auto !important;
    height: auto !important;
    object-fit: contain;
    border-radius: 0.5rem;
}
[class*="st-key-job_preview_multi_"] img {
    max-width: 100% !important;
    max-height: 240px !important;
    width: auto !important;
    height: auto !important;
    object-fit: contain;
    border-radius: 0.5rem;
}
</style>
"""


def _dismiss_job_button(job: Job, manager: JobManager) -> None:
    if st.button(
        i18n.t("jobs.dismiss"),
        key=f"dismiss_{job.id}",
        icon=":material/close:",
        width="stretch",
    ):
        manager.clear_finished([job.id])
        st.rerun()


def _render_job(job: Job, library: Library, on_asset: "Any | None" = None) -> None:
    manager = current_manager()
    # A running job gets one real, animated spinner via `st.status`; finished
    # jobs render in a bordered card.
    if job.active:
        frame = st.status(job.label, expanded=True, state="running")
    else:
        frame = st.container(border=True)
    with frame:
        with st.container(horizontal=True, vertical_alignment="center"):
            if not job.active:
                st.markdown(f"{status_icon(job.status)} **{job.label}**")
            st.badge(i18n.t(f"status.{job.status}"), color=status_color(job.status))
            if job.demo:
                st.badge(i18n.t("status.demo"), color="violet")
            st.caption(relative_time(job.created_at))

        if job.status == "failed":
            st.error(job.error or i18n.t("jobs.failed"), icon=":material/error:")

        if job.logs and job.active:
            with st.expander(i18n.t("common.logs"), icon=":material/terminal:"):
                st.code("\n".join(job.logs[-40:]), language=None)

        dismiss_rendered = False

        if job.status == "completed":
            assets = [a for a in (library.get(i) for i in job.asset_ids) if a]
            if job.summary:
                st.caption(job.summary)

            audios = [a for a in assets if a.kind == config.KIND_AUDIO]
            texts = [a for a in assets if a.kind == config.KIND_TEXT]
            visuals = [a for a in assets if a.is_visual]

            for asset in audios:
                st.audio(
                    library.bytes_of(asset),
                    format=asset.media_root or "audio/mpeg",
                    alt=asset.name,
                )
                inline_dismiss = len(audios) == 1 and not texts and not visuals
                with st.container(horizontal=True, gap="small"):
                    download_asset_button(
                        asset, library, key=f"jdl_{job.id}_{asset.id}", width="stretch"
                    )
                    if on_asset:
                        on_asset(asset, job)
                    if inline_dismiss:
                        _dismiss_job_button(job, manager)
                        dismiss_rendered = True

            for asset in texts:
                st.text_area(
                    i18n.t("common.transcript"),
                    value=library.text_of(asset),
                    height=220,
                    key=f"jtxt_{job.id}_{asset.id}",
                    label_visibility="collapsed",
                )
                srt = transcript_srt(asset)
                inline_dismiss = len(texts) == 1 and not audios and not visuals
                with st.container(horizontal=True, gap="small"):
                    download_asset_button(
                        asset, library, key=f"jdl_{job.id}_{asset.id}", width="stretch"
                    )
                    if srt:
                        st.download_button(
                            i18n.t("library.subtitles"),
                            data=srt,
                            file_name=f"{asset.name.rsplit('.', 1)[0]}.srt",
                            mime="text/plain",
                            key=f"jsrt_{job.id}_{asset.id}",
                            icon=":material/subtitles:",
                            width="stretch",
                        )
                    if inline_dismiss:
                        _dismiss_job_button(job, manager)
                        dismiss_rendered = True

            if len(visuals) == 1:
                asset = visuals[0]
                with st.container(key=f"job_preview_single_{job.id}"):
                    asset_image(asset, library, width=360)
                inline_dismiss = not audios and not texts
                with st.container(horizontal=True, gap="small"):
                    download_asset_button(
                        asset, library, key=f"jdl_{job.id}_{asset.id}", width="stretch"
                    )
                    if on_asset:
                        on_asset(asset, job)
                    if inline_dismiss:
                        _dismiss_job_button(job, manager)
                        dismiss_rendered = True
            elif len(visuals) > 1:
                preview_grid = st.grid(2, gap="small")
                for index, asset in enumerate(visuals):
                    with preview_grid.cell():
                        with st.container(key=f"job_preview_multi_{job.id}_{index}"):
                            asset_image(asset, library, width="stretch")
                        with st.container(horizontal=True, gap="small"):
                            download_asset_button(
                                asset,
                                library,
                                key=f"jdl_{job.id}_{asset.id}",
                                width="stretch",
                            )
                            if on_asset:
                                on_asset(asset, job)

        if job.active:
            if st.button(
                i18n.t("jobs.cancel"),
                key=f"cancel_{job.id}",
                icon=":material/stop_circle:",
                width="stretch",
            ):
                manager.cancel(job.id)
                st.rerun()
        elif not dismiss_rendered:
            _dismiss_job_button(job, manager)


def render_jobs(
    job_ids: list[str],
    *,
    title: str | None = None,
    on_asset: "Any | None" = None,
) -> None:
    """Render a list of jobs (newest first) with live output previews."""
    manager = current_manager()
    library = current_library()
    jobs = manager.list_jobs(job_ids)
    if title:
        st.subheader(title, icon=":material/queue:")
    if not jobs:
        st.caption(i18n.t("jobs.none"))
        return
    st.html(_JOB_PREVIEW_CSS)
    for job in jobs:
        _render_job(job, library, on_asset)


@st.fragment(run_every=2)
def _live_jobs(job_ids: list[str], title: str | None, on_asset: "Any | None") -> None:
    i18n.set_language(current_language())
    render_jobs(job_ids, title=title, on_asset=on_asset)
    if not current_manager().active_jobs(job_ids):
        # Everything finished — hand back to a static render.
        st.rerun()


def jobs_panel(
    job_ids: list[str],
    *,
    title: str | None = None,
    on_asset: "Any | None" = None,
) -> None:
    """Auto-refreshing job panel that stops polling once everything settles."""
    if not job_ids:
        return
    resolved = title if title is not None else i18n.t("jobs.session")
    if current_manager().active_jobs(job_ids):
        _live_jobs(job_ids, resolved, on_asset)
    else:
        render_jobs(job_ids, title=resolved, on_asset=on_asset)
