"""Smoke tests for the pieces that don't need a Streamlit runtime.

They cover the on-disk library, profile auth, translations, slug naming and the
``secrets.toml`` merge that self-hosters depend on. Run with ``uv run pytest``.
"""

from __future__ import annotations

import pytest

from vertigo import auth, config, i18n, naming, oidc
from vertigo.store import Library


@pytest.fixture(autouse=True)
def vertigo_home(tmp_path, monkeypatch):
    """Point every test at a throwaway data directory."""
    monkeypatch.setenv("VERTIGO_HOME", str(tmp_path))
    for name in (
        "VERTIGO_OIDC_ENABLED",
        "VERTIGO_OIDC_CLIENT_ID",
        "VERTIGO_OIDC_CLIENT_SECRET",
        "VERTIGO_PUBLIC_URL",
        "VERTIGO_SECRETS_DIR",
        "VERTIGO_DEMO",
        "FAL_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_base_home_follows_env(vertigo_home):
    assert config.base_home() == vertigo_home


def test_library_roundtrip(tmp_path):
    home = tmp_path / "library"
    library = Library(home)
    asset = library.add_file(
        b"hello world",
        kind=config.KIND_TEXT,
        name="note.txt",
        media_type="text/plain",
    )

    assert library.text_of(asset) == "hello world"
    assert library.counts()["total"] == 1
    # A fresh instance reads the persisted metadata back from disk.
    assert Library(home).get(asset.id).name == "note.txt"

    assert library.delete(asset.id) is True
    assert Library(home).get(asset.id) is None
    assert not library.file_path(asset).exists()


def test_seed_profiles_and_pin_auth(vertigo_home):
    auth.ensure_users()
    users = auth.list_users()
    assert len(users) == 3
    assert users[0].is_admin is True

    first = users[0].id
    assert auth.authenticate(first, auth.DEFAULT_PIN) is True
    assert auth.authenticate(first, "9999") is False

    auth.set_pin(first, "2468")
    assert auth.authenticate(first, "2468") is True
    assert auth.authenticate(first, auth.DEFAULT_PIN) is False


def test_telegram_user_is_stable(vertigo_home):
    created = auth.ensure_telegram_user("42", "Ada", "ada")
    assert created.telegram_id == "42"
    again = auth.ensure_telegram_user("42", "Ada", "ada")
    assert again.id == created.id
    assert auth.user_by_telegram("42").id == created.id


def test_translations_switch():
    i18n.set_language("ru")
    assert i18n.t("nav.home") == "Главная"
    i18n.set_language("en")
    assert i18n.t("nav.home") == "Home"
    assert i18n.t("missing.key") == "missing.key"


def test_slugify_transliterates_cyrillic():
    assert naming.slugify("Привет, мир!") == "privet-mir"
    assert naming.slugify("") == "output"


def test_secrets_merge_preserves_unrelated_keys(monkeypatch):
    monkeypatch.setenv("VERTIGO_PUBLIC_URL", "https://vertigo.example.com")
    monkeypatch.setenv("VERTIGO_OIDC_CLIENT_ID", "123")
    monkeypatch.setenv("VERTIGO_OIDC_CLIENT_SECRET", "shh")

    existing = 'FAL_KEY = "my-fal-key"\n\n[auth]\nredirect_uri = "old"\ncookie_secret = "old"\n'
    merged = oidc.render_secrets(existing)

    assert 'FAL_KEY = "my-fal-key"' in merged
    assert "https://vertigo.example.com/oauth2callback" in merged
    assert 'client_id = "123"' in merged
    assert "old" not in merged
    # Rendering again is a no-op, so restarts don't churn the file.
    assert oidc.render_secrets(merged) == merged


def test_write_secrets_respects_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VERTIGO_OIDC_ENABLED", "1")
    monkeypatch.setenv("VERTIGO_OIDC_CLIENT_ID", "1")
    monkeypatch.setenv("VERTIGO_OIDC_CLIENT_SECRET", "x")

    path = oidc.write_secrets()
    assert path == tmp_path / ".streamlit" / "secrets.toml"
    assert path.exists()
    assert oidc.secrets_path() == path


def test_write_secrets_noop_when_disabled(vertigo_home):
    assert oidc.write_secrets() is None


def test_brand_assets_are_packaged():
    """The generated brand files exist at the sizes the app and PWA expect."""
    from PIL import Image

    icons = config.STATIC_DIR / "icons"
    expected = {
        config.LOGO_FILE: 1024,
        config.FAVICON_FILE: 256,
        icons / "icon-192.png": 192,
        icons / "icon-512.png": 512,
        icons / "icon-maskable-512.png": 512,
        icons / "apple-touch-icon.png": 180,
    }
    for path, size in expected.items():
        assert path.exists(), path
        with Image.open(path) as image:
            assert image.size == (size, size), path


def test_literal_translations_resolve_in_every_language():
    """Every ``i18n.t("...")`` literal in the source must exist in all locales."""
    import re
    from pathlib import Path

    pattern = re.compile(r'i18n\.t\(\s*"([^"]+)"')
    source = Path(i18n.__file__).resolve().parent
    keys: set[str] = set()
    for path in source.rglob("*.py"):
        keys.update(pattern.findall(path.read_text(encoding="utf-8")))

    assert keys
    for language in i18n.LANGUAGES:
        i18n.set_language(language)
        missing = sorted(key for key in keys if i18n.t(key) == key)
        assert missing == [], f"{language}: {missing}"


def test_model_catalog_and_endpoints():
    image_ids = [m["id"] for m in config.IMAGE_MODELS]
    assert image_ids == ["gpt-image-2.6", "nano-banana-2.1", "ideogram-4.6"]
    assert config.model_by_id("gpt-image-2.6")["t2i"] == "openai/gpt-image-2.6/sunburst/text-to-image"
    assert config.model_by_id("gpt-image-2.6")["edit"] == "openai/gpt-image-2.6/sunburst/edit"
    assert config.model_by_id("nano-banana-2.1")["t2i"] == "google/nano-banana-2.1"
    assert config.model_by_id("nano-banana-2.1")["edit"] == "google/nano-banana-2.1/edit"
    assert config.model_by_id("ideogram-4.6")["t2i"] == "ideogram/v4.6"
    assert config.model_by_id("ideogram-4.6")["edit"] == "ideogram/v4.6/edit"
    # Unknown model IDs fall back cleanly instead of crashing.
    assert config.model_by_id("gpt-image-2.5")["id"] == "gpt-image-2.6"

    tts_ids = [m["id"] for m in config.TTS_MODELS]
    assert tts_ids == ["eleven-v4", "gemini-3.8-flash-tts"]
    assert config.tts_model_by_id("eleven-v4")["endpoint"] == "elevenlabs/tts/eleven-v4"
    assert config.tts_model_by_id("gemini-3.8-flash-tts")["endpoint"] == "google/gemini-3.8-flash-tts"
    assert len(config.GEMINI_TTS_VOICES) == 30
    assert config.GEMINI_TTS_VOICES[0] == "Kore"
    assert config.endpoint_label("ideogram/v4.6/edit") == "Ideogram 4.6"
    assert config.endpoint_label("google/gemini-3.8-flash-tts") == "Gemini 3.8 Flash"


def test_param_icons_are_valid_material_icons():
    from streamlit.material_icon_names import ALL_MATERIAL_ICONS

    from vertigo import ui

    cases = [
        ("image_size", [v for _, v in config.GPT_IMAGE_SIZES]),
        ("aspect_ratio", config.NANO_ASPECT_RATIOS),
        ("quality", config.GPT_QUALITIES + config.IDEOGRAM_QUALITIES),
        ("background", config.GPT_BACKGROUNDS + config.IDEOGRAM_BACKGROUNDS),
        ("resolution", config.NANO_RESOLUTIONS),
        ("reasoning_effort", config.GPT_REASONING_EFFORTS),
        ("thinking_level", config.NANO_THINKING_LEVELS),
        ("edit_precision", config.IDEOGRAM_EDIT_PRECISIONS),
        ("safety_tolerance", config.SAFETY_TOLERANCES),
        ("output_format", config.IMAGE_OUTPUT_FORMATS),
        ("audio_format", config.ELEVEN_OUTPUT_FORMATS),
        ("voice", ["Rachel", "Kore"]),
        ("text_normalization", config.TEXT_NORMALIZATION),
        ("vector_style", [v for _, v in config.VECTOR_STYLES]),
        ("upscale_model", [m["id"] for m in config.UPSCALE_MODELS]),
        ("topaz_model", config.TOPAZ_MODELS),
    ]
    for param_type, values in cases:
        for value in values:
            icon = ui.param_icon(param_type, value)
            assert icon.startswith(":material/") and icon.endswith(":"), (param_type, value, icon)
            name = icon[len(":material/") : -1]
            assert name in ALL_MATERIAL_ICONS, f"Invalid material icon {name!r} for {param_type}={value!r}"

    # Distinct values in the same parameter type get distinct icons.
    assert ui.param_icon("image_size", "square_hd") != ui.param_icon("image_size", "landscape_16_9")
    assert ui.param_icon("image_size", "landscape_16_9") != ui.param_icon("image_size", "portrait_4_3")
    assert ui.param_icon("background", "transparent") != ui.param_icon("background", "opaque")
    assert ui.param_icon("resolution", "1K") != ui.param_icon("resolution", "4K")
    assert ui.param_icon("quality", "low") != ui.param_icon("quality", "high")


def test_dynamic_model_translations_exist_in_every_language():
    expected = [
        *(f"models.{m['id']}.tagline" for m in config.IMAGE_MODELS),
        *(f"models.{m['id']}.tagline" for m in config.TTS_MODELS),
        *(f"models.{m['id']}.label" for m in config.UPSCALE_MODELS),
    ]
    for language in i18n.LANGUAGES:
        i18n.set_language(language)
        missing = [k for k in expected if i18n.t(k) == k]
        assert missing == [], f"{language}: {missing}"


def test_streamlit_pages_render_without_errors(vertigo_home):
    """Run the studio, speech and enhance pages through Streamlit's AppTest."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    auth.ensure_users()
    admin = auth.list_users()[0]
    pages_dir = Path(config.__file__).resolve().parent / "app_pages"

    # Entrypoint renders Home through st.navigation cleanly without page subtitles.
    entrypoint = Path(config.__file__).resolve().parent / "streamlit_app.py"
    at_main = AppTest.from_file(str(entrypoint))
    at_main.session_state["user_id"] = admin.id
    at_main.session_state["user_name"] = admin.name
    at_main.run(timeout=15)
    assert not at_main.exception, f"streamlit_app.py raised: {at_main.exception}"

    for page_name in ("studio.py", "speech.py", "enhance.py", "library.py", "settings.py"):
        at = AppTest.from_file(str(pages_dir / page_name))
        at.session_state["user_id"] = admin.id
        at.session_state["user_name"] = admin.name
        at.session_state["studio_refs"] = []
        at.session_state["studio_jobs"] = []
        at.session_state["enhance_jobs"] = []
        at.session_state["speech_jobs"] = []
        at.run(timeout=15)
        assert not at.exception, f"{page_name} raised: {at.exception}"

        # Ensure no pricing or endpoint captions leak into the UI.
        captions = [c.value for c in at.caption]
        for caption in captions:
            assert "Endpoint:" not in caption, (page_name, caption)
            assert "$" not in caption, (page_name, caption)


def test_studio_and_speech_models_and_dynamic_selectbox_icons(vertigo_home):
    """Switching models and selectbox values updates selectbox icons cleanly."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    auth.ensure_users()
    admin = auth.list_users()[0]
    pages_dir = Path(config.__file__).resolve().parent / "app_pages"

    def _run_page(page_name: str, state: dict) -> AppTest:
        at = AppTest.from_file(str(pages_dir / page_name))
        at.session_state["user_id"] = admin.id
        at.session_state["user_name"] = admin.name
        at.session_state["studio_refs"] = []
        at.session_state["studio_jobs"] = []
        at.session_state["enhance_jobs"] = []
        at.session_state["speech_jobs"] = []
        for key, value in state.items():
            at.session_state[key] = value
        at.run(timeout=15)
        assert not at.exception, f"{page_name} ({state}) raised: {at.exception}"
        return at

    # Studio: every image model renders parameter selectboxes with Material icons.
    for model_id in ("gpt-image-2.6", "nano-banana-2.1", "ideogram-4.6"):
        at = _run_page("studio.py", {"studio_model": model_id})
        assert len(at.selectbox) >= 4
        for sb in at.selectbox:
            assert sb.proto.icon.startswith(":material/"), (model_id, sb.label, sb.proto.icon)

    # Dynamic icon update on GPT Image 2.6 Image size & Background selectboxes.
    at = _run_page("studio.py", {"studio_model": "gpt-image-2.6"})
    size_sb = at.selectbox(key="studio_size_gpt-image-2.6")
    assert size_sb.proto.icon == ":material/aspect_ratio:"
    size_sb.select("Landscape 16:9").run(timeout=15)
    assert at.selectbox(key="studio_size_gpt-image-2.6").proto.icon == ":material/crop_16_9:"
    at.selectbox(key="studio_bg_gpt-image-2.6").select("transparent").run(timeout=15)
    assert at.selectbox(key="studio_bg_gpt-image-2.6").proto.icon == ":material/grid_on:"

    # Dynamic icon update on Nano Banana 2.1 Resolution selectbox.
    at_nano = _run_page("studio.py", {"studio_model": "nano-banana-2.1"})
    assert at_nano.selectbox(key="studio_res_nano-banana-2.1").proto.icon == ":material/hd:"
    at_nano.selectbox(key="studio_res_nano-banana-2.1").select("4K").run(timeout=15)
    assert at_nano.selectbox(key="studio_res_nano-banana-2.1").proto.icon == ":material/4k:"

    # Vector mode selectboxes also have icons.
    at_vec = _run_page("studio.py", {"studio_mode": "vector"})
    assert len(at_vec.selectbox) == 2
    for sb in at_vec.selectbox:
        assert sb.proto.icon.startswith(":material/")

    # Speech: both Eleven v4 and Gemini 3.8 Flash TTS render with icons.
    for model_id in ("eleven-v4", "gemini-3.8-flash-tts"):
        at_tts = _run_page("speech.py", {"tts_model": model_id})
        assert len(at_tts.selectbox) >= 1
        for sb in at_tts.selectbox:
            assert sb.proto.icon.startswith(":material/"), (model_id, sb.label, sb.proto.icon)

    # Speech STT mode renders cleanly with no pricing captions.
    at_stt = _run_page("speech.py", {"speech_mode": "stt"})
    for caption in [c.value for c in at_stt.caption]:
        assert "$" not in caption

    # Enhance Upscale (Topaz) selectboxes all have icons.
    at_enh = _run_page(
        "enhance.py",
        {
            "enhance_mode": "upscale",
            "upscale_model": i18n.t("models.topaz-generative.label"),
        },
    )
    assert len(at_enh.selectbox) == 3
    for sb in at_enh.selectbox:
        assert sb.proto.icon.startswith(":material/")


def test_request_builders_match_model_schemas(vertigo_home):
    """Verify endpoint routing and payloads for all image and TTS engines."""
    import ast
    from pathlib import Path

    # Load pure builder functions by executing only their definitions without Streamlit side effects.
    pages_dir = Path(config.__file__).resolve().parent / "app_pages"

    def _load_fn(filename: str, fn_name: str):
        source = (pages_dir / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        mod = ast.Module(body=[fn_node], type_ignores=[])
        ns = {"Any": object}
        exec(compile(mod, filename, "exec"), ns)
        return ns[fn_name]

    build_image = _load_fn("studio.py", "_build_image_request")
    build_tts = _load_fn("speech.py", "_build_tts_request")

    # GPT Image 2.6 T2I & Edit
    gpt = config.model_by_id("gpt-image-2.6")
    gpt_params = {
        "num_images": 2,
        "image_size": "landscape_16_9",
        "quality": "high",
        "reasoning_effort": "medium",
        "background": "transparent",
        "output_format": "png",
    }
    ep, args = build_image(gpt, "  Neon sign  ", gpt_params, [])
    assert ep == "openai/gpt-image-2.6/sunburst/text-to-image"
    assert args["reasoning_effort"] == "medium"
    assert "image_urls" not in args

    ep, args = build_image(gpt, "Fix text", gpt_params, ["https://example.com/a.png"])
    assert ep == "openai/gpt-image-2.6/sunburst/edit"
    assert args["image_urls"] == ["https://example.com/a.png"]

    # Nano Banana 2.1 T2I & Edit
    nano = config.model_by_id("nano-banana-2.1")
    nano_params = {
        "num_images": 1,
        "aspect_ratio": "16:9",
        "resolution": "2K",
        "thinking_level": "high",
        "safety_tolerance": "4",
        "output_format": "webp",
        "enable_web_search": True,
    }
    ep, args = build_image(nano, "Sunset", nano_params, [])
    assert ep == "google/nano-banana-2.1"
    assert args["thinking_level"] == "high"
    assert args["enable_web_search"] is True

    ep, args = build_image(nano, "Restyle", nano_params, ["https://example.com/1.png"])
    assert ep == "google/nano-banana-2.1/edit"
    assert args["image_urls"] == ["https://example.com/1.png"]

    # Ideogram 4.6 T2I & Edit/I2I (single and multi-ref)
    ideo = config.model_by_id("ideogram-4.6")
    ideo_params = {
        "num_images": 1,
        "image_size": "square_hd",
        "quality": "high",
        "background": "transparent",
        "edit_precision": "high",
    }
    ep, args = build_image(ideo, "Logo", ideo_params, [])
    assert ep == "ideogram/v4.6"
    assert args["transparent_background"] is True
    assert "edit_precision" not in args

    ep, args = build_image(
        ideo,
        "Change background",
        ideo_params,
        ["https://example.com/base.png", "https://example.com/ref.png"],
    )
    assert ep == "ideogram/v4.6/edit"
    assert args["image_url"] == "https://example.com/base.png"
    assert args["reference_image_urls"] == ["https://example.com/ref.png"]
    assert args["edit_precision"] == "high"
    assert "image_urls" not in args

    # Eleven v4 TTS
    eleven = config.tts_model_by_id("eleven-v4")
    ep, args = build_tts(
        eleven,
        "Hello world",
        {
            "voice": "Rachel",
            "stability": 0.5,
            "similarity_boost": 0.75,
            "apply_text_normalization": "auto",
            "output_format": "mp3_44100_128",
            "language_code": "en",
        },
    )
    assert ep == "elevenlabs/tts/eleven-v4"
    assert args["text"] == "Hello world"
    assert "speed" not in args and "style" not in args

    # Gemini 3.8 Flash TTS
    gem_tts = config.tts_model_by_id("gemini-3.8-flash-tts")
    ep, args = build_tts(
        gem_tts,
        "Have a wonderful day!",
        {
            "voice": "Kore",
            "style_instructions": "Warm and cheerful",
            "language_code": "en-US",
        },
    )
    assert ep == "google/gemini-3.8-flash-tts"
    assert args == {
        "prompt": "Have a wonderful day!",
        "voice": "Kore",
        "style_instructions": "Warm and cheerful",
        "language_code": "en-US",
    }


def test_demo_mode_end_to_end_all_workflows(vertigo_home, monkeypatch):
    """Every workflow completes locally in Demo Mode with valid media and zero fal calls."""
    import io
    import time
    import wave

    from PIL import Image

    from vertigo import demo, fal, ui
    from vertigo.jobs import SAVE_AUDIO, SAVE_IMAGE, SAVE_IMAGES, SAVE_TRANSCRIPT, JobManager

    monkeypatch.setattr(demo, "DELAY_SCALE", 0.0)
    assert demo.is_enabled() is False
    assert fal.ready() is False

    demo.set_enabled(True)
    assert demo.is_enabled() is True
    assert fal.ready() is True

    # Ensure env override works and marks env_forced().
    monkeypatch.setenv("VERTIGO_DEMO", "1")
    assert demo.env_forced() is True
    assert demo.is_enabled() is True

    library = Library(vertigo_home / "demo_lib")
    manager = JobManager(library, max_workers=2)

    def _wait(job_id: str):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job and not job.active:
                return job
            time.sleep(0.02)
        raise AssertionError(f"Job {job_id} did not finish in time")

    # 1. Image T2I (transparent, 2 images, 16:9)
    jid = manager.submit(
        kind="image",
        label="GPT Image 2.6 — generation",
        endpoint="openai/gpt-image-2.6/sunburst/text-to-image",
        arguments={
            "prompt": "Floating glass orb",
            "num_images": 2,
            "image_size": "landscape_16_9",
            "background": "transparent",
            "output_format": "png",
        },
        save_kind=SAVE_IMAGES,
    )
    job = _wait(jid)
    assert job.status == "completed" and job.demo is True
    assert len(job.asset_ids) == 2
    first_img = library.get(job.asset_ids[0])
    assert first_img is not None and first_img.kind == config.KIND_IMAGE
    with Image.open(io.BytesIO(library.bytes_of(first_img))) as im:
        assert im.mode == "RGBA"
        assert im.width > im.height

    # Demo CDN helper returns synthetic URL without touching fal or mutating remote_url.
    assert fal.ensure_remote_url(first_img, library) == f"demo://asset/{first_img.id}"
    assert library.get(first_img.id).remote_url == ""

    # 2. Image Edit (Ideogram 4.6)
    jid_edit = manager.submit(
        kind="image",
        label="Ideogram 4.6 — edit",
        endpoint="ideogram/v4.6/edit",
        arguments={
            "prompt": "Warm sunset glow",
            "num_images": 1,
            "image_url": f"demo://asset/{first_img.id}",
            "image_size": "auto",
            "quality": "high",
            "edit_precision": "high",
        },
        save_kind=SAVE_IMAGES,
        parent_id=first_img.id,
    )
    job_edit = _wait(jid_edit)
    assert job_edit.status == "completed" and len(job_edit.asset_ids) == 1

    # 3. Vector T2V & Trace
    jid_vec = manager.submit(
        kind="vector",
        label="Recraft vector",
        endpoint=config.VECTOR_ENDPOINT_T2V,
        arguments={"prompt": "Minimal wave", "image_size": "square_hd"},
        save_kind=SAVE_IMAGES,
    )
    job_vec = _wait(jid_vec)
    assert job_vec.status == "completed"
    vec_asset = library.get(job_vec.asset_ids[0])
    assert vec_asset is not None and vec_asset.is_svg
    assert "<svg" in library.text_of(vec_asset)

    jid_trace = manager.submit(
        kind="vector",
        label="Vectorise",
        endpoint=config.VECTOR_ENDPOINT_TRACE,
        arguments={"image_url": f"demo://asset/{first_img.id}"},
        save_kind=SAVE_IMAGE,
        parent_id=first_img.id,
    )
    assert _wait(jid_trace).status == "completed"

    # 4. Enhance: Remove background & Upscale
    jid_bg = manager.submit(
        kind="remove_bg",
        label="Remove background",
        endpoint=config.REMOVE_BG_ENDPOINT,
        arguments={"image_url": f"demo://asset/{first_img.id}"},
        save_kind=SAVE_IMAGE,
        parent_id=first_img.id,
    )
    job_bg = _wait(jid_bg)
    assert job_bg.status == "completed"
    bg_asset = library.get(job_bg.asset_ids[0])
    with Image.open(io.BytesIO(library.bytes_of(bg_asset))) as im:
        assert im.mode == "RGBA"

    jid_up = manager.submit(
        kind="upscale",
        label="Topaz upscale",
        endpoint="fal-ai/topaz/upscale/image",
        arguments={"image_url": f"demo://asset/{first_img.id}", "upscale_factor": 2.0, "output_format": "png"},
        save_kind=SAVE_IMAGE,
        parent_id=first_img.id,
    )
    job_up = _wait(jid_up)
    assert job_up.status == "completed"
    up_asset = library.get(job_up.asset_ids[0])
    with Image.open(io.BytesIO(library.bytes_of(up_asset))) as im:
        assert im.width >= first_img.meta["width"]

    # 5. Speech TTS (Eleven v4 & Gemini 3.8 Flash)
    for tts_ep, tts_args in (
        ("elevenlabs/tts/eleven-v4", {"text": "Hello from Eleven v4", "voice": "Rachel"}),
        ("google/gemini-3.8-flash-tts", {"prompt": "Hello from Gemini", "voice": "Kore"}),
    ):
        jid_tts = manager.submit(
            kind="tts",
            label="Speech",
            endpoint=tts_ep,
            arguments=tts_args,
            save_kind=SAVE_AUDIO,
            name_hint="hello",
        )
        job_tts = _wait(jid_tts)
        assert job_tts.status == "completed"
        audio_asset = library.get(job_tts.asset_ids[0])
        assert audio_asset is not None and audio_asset.kind == config.KIND_AUDIO
        with wave.open(io.BytesIO(library.bytes_of(audio_asset)), "rb") as wf:
            assert wf.getnframes() > 0

    # 6. Speech STT (transcript + SRT)
    jid_stt = manager.submit(
        kind="stt",
        label="Transcribe",
        endpoint=config.STT_ENDPOINT,
        arguments={"audio_url": "demo://asset/x", "diarize": True},
        save_kind=SAVE_TRANSCRIPT,
    )
    job_stt = _wait(jid_stt)
    assert job_stt.status == "completed"
    txt_asset = library.get(job_stt.asset_ids[0])
    assert txt_asset is not None and txt_asset.kind == config.KIND_TEXT
    assert ui.transcript_srt(txt_asset) is not None


def test_studio_reference_tray_and_session_ui_in_demo_mode(vertigo_home, monkeypatch):
    """Reference tray CCv2 and refined This session cards render cleanly in AppTest."""
    import io
    import time
    from pathlib import Path

    from PIL import Image
    from streamlit.testing.v1 import AppTest

    from vertigo import demo
    from vertigo.jobs import SAVE_IMAGES, get_manager
    from vertigo.store import get_library

    monkeypatch.setattr(demo, "DELAY_SCALE", 0.0)
    monkeypatch.setenv("VERTIGO_DEMO", "1")

    auth.ensure_users()
    admin = auth.list_users()[0]
    library = get_library(admin.id)
    manager = get_manager(admin.id)

    # Seed two reference images in the user's library.
    ref_ids = []
    for idx, color in enumerate(((240, 120, 60, 255), (60, 140, 240, 255))):
        buf = io.BytesIO()
        Image.new("RGBA", (128, 128), color).save(buf, format="PNG")
        asset = library.add_file(
            buf.getvalue(),
            kind=config.KIND_IMAGE,
            name=f"ref-{idx + 1}.png",
            origin=config.ORIGIN_REFERENCE,
            media_type="image/png",
        )
        ref_ids.append(asset.id)

    # Complete a demo single-image job so This session renders with full action row.
    job_id = manager.submit(
        kind="image",
        label="GPT Image 2.6 — generation",
        endpoint="openai/gpt-image-2.6/sunburst/text-to-image",
        arguments={"prompt": "Demo render", "num_images": 1, "image_size": "square_hd"},
        save_kind=SAVE_IMAGES,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and manager.is_active(job_id):
        time.sleep(0.02)
    assert manager.get(job_id).status == "completed"

    pages_dir = Path(config.__file__).resolve().parent / "app_pages"
    at = AppTest.from_file(str(pages_dir / "studio.py"))
    at.session_state["user_id"] = admin.id
    at.session_state["user_name"] = admin.name
    at.session_state["studio_refs"] = list(ref_ids)
    at.session_state["studio_jobs"] = [job_id]
    at.session_state["enhance_jobs"] = []
    at.session_state["speech_jobs"] = []
    at.run(timeout=15)
    assert not at.exception, f"studio.py raised: {at.exception}"

    # Generate button is enabled in Demo Mode even without a FAL_KEY (once prompt entered).
    assert not at.warning, "No API key warning should appear in Demo Mode"
    # Completed job renders Download, Edit / reuse, and Dismiss buttons.
    button_labels = [b.label for b in at.button]
    assert i18n.t("studio.edit_reuse") in button_labels
    assert i18n.t("jobs.dismiss") in button_labels
