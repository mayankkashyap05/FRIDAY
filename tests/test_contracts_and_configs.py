"""Cross-module integrity, tool registry parity, and config validation tests."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _ensure_google_genai_stub():
    """Provide minimal google.genai stub if not installed in CI."""
    if "google.genai" in sys.modules:
        return
    google_mod = sys.modules.setdefault("google", types.ModuleType("google"))
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class _Dummy:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    for attr in ("LiveConnectConfig", "FunctionResponse", "Tool", "FunctionDeclaration",
                 "Schema", "Type", "Content", "Part", "GenerateContentConfig"):
        setattr(types_mod, attr, _Dummy)
    genai_mod.types = types_mod
    genai_mod.Client = MagicMock
    google_mod.genai = genai_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod


def test_config_models_exports_and_no_stale_models():
    from config import models

    for attr in ("MODEL", "LITE_MODEL", "MODEL_FLASH", "MODEL_FLASH_LITE", "MODEL_LIVE"):
        val = getattr(models, attr, None)
        assert isinstance(val, str) and len(val) > 0, f"Missing {attr}"

    stale_patterns = ("gemini-2.5-flash-lite", "gemini-3.8-flash", "gemini-2.0-flash")
    for py_file in ROOT.rglob("*.py"):
        rel = py_file.relative_to(ROOT).as_posix()
        if (
            rel.startswith(("tests/", "_archive/", ".venv/", "venv/", "vendor/", "versions/", "node_modules/", ".git/"))
            or rel == "config/models.py"
        ):
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        for stale in stale_patterns:
            assert stale not in text, f"Stale model '{stale}' found in {rel}"


def test_config_audio_timing_invariants():
    from config import audio

    assert audio.AUDIO_END_GRACE_S > 0
    assert audio.SPEAKING_WATCHDOG_S > audio.AUDIO_END_GRACE_S
    assert 0 < audio.SPEAKING_WATCHDOG_TICK_S <= audio.SPEAKING_WATCHDOG_S


def test_config_json_files_valid_and_no_real_secrets():
    cfg_dir = ROOT / "config"
    secret_re = re.compile(r"(AIza[0-9A-Za-z\-_]{35}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})")
    for jf in cfg_dir.glob("*.json"):
        raw = jf.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, (dict, list))
        assert not secret_re.search(raw), f"Potential real secret in {jf.name}"


def test_safety_tool_risk_and_schema_parity():
    import safety

    # Every high-risk tool in TOOL_RISK should be classified HIGH
    for tool, risk in safety.TOOL_RISK.items():
        if risk == safety.Risk.HIGH:
            assert safety.classify(tool, {}) == safety.Risk.HIGH

    # clear_memory and forget_memory must be explicitly registered
    assert safety.TOOL_RISK["forget_memory"] == safety.Risk.MEDIUM
    assert safety.TOOL_RISK["clear_memory"] == safety.Risk.HIGH


def test_prompt_txt_modes_moods_and_voices_parity():
    prompt_path = ROOT / "core" / "prompt.txt"
    text = prompt_path.read_text(encoding="utf-8")
    for mode in ("normal", "girlfriend", "crazy_friend", "butler", "friend", "casual"):
        assert mode in text, f"Mode '{mode}' missing from core/prompt.txt"
    for mood in ("calm", "playful", "romantic", "strict"):
        assert mood in text, f"Mood '{mood}' missing from core/prompt.txt"


@pytest.mark.parametrize("opacity_in,expected", [
    (0, 30), (29, 30), (30, 30), (75, 75), (100, 100), (150, 100), ("bad", 100),
])
def test_settings_store_opacity_clamping(tmp_path, opacity_in, expected):
    from ui.settings_store import SettingsStore

    store = SettingsStore(path=tmp_path / "settings.json")
    store.update(opacity=opacity_in)
    assert store.values["opacity"] == expected


def test_settings_store_corrupted_json_recovery(tmp_path):
    from ui.settings_store import SettingsStore

    p = tmp_path / "settings.json"
    p.write_text("{broken json", encoding="utf-8")
    store = SettingsStore(path=p)
    assert isinstance(store.values["opacity"], int)
    store.update(theme="dark")
    assert store.values["theme"] == "dark"
