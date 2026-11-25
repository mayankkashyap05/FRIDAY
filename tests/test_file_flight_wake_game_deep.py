"""Offline deep tests for file_controller, flight_finder, friday_wake, game_updater."""

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import file_controller, flight_finder, friday_wake, game_updater
from config.models import MODEL_FLASH_LITE


# ── file controller ─────────────────────────────────────────────────────────
def test_ai_categorize_uses_supported_model_path(monkeypatch):
    seen = {}

    def fake_generate(model, contents, **kwargs):
        seen["model"] = model
        seen["contents"] = contents
        return SimpleNamespace(text='{"invoice.pdf": "Finance"}')

    monkeypatch.setattr(file_controller, "generate_with_retry", fake_generate)
    result = file_controller._ai_categorize(["invoice.pdf"], "Documents")
    assert result == {"invoice.pdf": "Finance"}
    assert seen["model"] == MODEL_FLASH_LITE
    assert "Finance" in seen["contents"]


def test_system_dirs_skipped_and_delete_guards_root(tmp_path, monkeypatch):
    assert file_controller._is_system_path(Path("/home/user/.git/config"))
    assert file_controller._is_system_path(Path("/usr/lib/python3.11/site-packages/x.py"))
    called = []
    monkeypatch.setattr(file_controller.send2trash, "send2trash",
                        lambda path: called.append(path), raising=False)
    assert "Refusing" in file_controller.delete_file(str(Path.home()))
    assert called == []


def test_organize_and_undo_cleans_empty_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(file_controller, "ORGANIZE_LOG_PATH", tmp_path / "log.json")
    source = tmp_path / "Desktop"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"img")
    (source / "notes.txt").write_text("hello")
    assert "Moved 2" in file_controller.organize_folder(scope=str(source))
    assert (source / "Images" / "photo.jpg").exists()
    assert "Restored 2" in file_controller.undo_organize()
    assert (source / "photo.jpg").exists()
    assert not (source / "Images").exists()


def test_duplicates_search_content_recent_and_atomic_write(tmp_path, monkeypatch):
    monkeypatch.setattr(file_controller, "ORGANIZE_LOG_PATH", tmp_path / "log.json")
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("meeting invoice 42")
    b.write_text("meeting invoice 42")
    duplicates = file_controller.find_duplicates(str(tmp_path), recursive=False)
    assert "duplicate group" in duplicates
    matches = file_controller.search_content(str(tmp_path), text="invoice")
    assert "a.txt" in matches and "invoice" in matches
    assert "a.txt" in file_controller.recent_files(str(tmp_path), hours=1)
    assert "Written to" in file_controller.write_file(str(tmp_path / "new.txt"), "ok")
    assert not list(tmp_path.glob("friday-write-*.tmp"))


def test_file_controller_string_booleans(tmp_path):
    result = file_controller.file_controller({"action": "organize", "scope": str(tmp_path),
                                              "dry_run": "true", "include_subfolders": "false"})
    assert "Would move" in result or "No files" in result


# ── flight finder ───────────────────────────────────────────────────────────
def test_flight_date_parsing_is_deterministic():
    assert flight_finder._parse_date("15/03/2030") == "2030-03-15"
    assert flight_finder._parse_date("2031-01-02") == "2031-01-02"
    tomorrow = flight_finder._parse_date("tomorrow")
    assert len(tomorrow) == 10 and tomorrow[4] == "-"


def test_flight_url_defaults_inr_and_trip_options():
    url = flight_finder._build_google_flights_url(
        "New York", "Los Angeles", "2030-12-01", "2030-12-10",
        passengers=2, cabin="business", non_stop=True, sort_by="duration")
    assert "New+York" in url and "Los+Angeles" in url
    assert "curr=INR" in url and "cabin=3" in url
    assert "nonstop" in url and "2 passengers" in url and "duration" in url


def test_flight_extraction_filters_sorts_and_uses_flash(monkeypatch):
    seen = {}

    def fake_generate(model, contents, **kwargs):
        seen["model"] = model
        return SimpleNamespace(text=json.dumps([
            {"airline": "Air A", "stops": 1, "price": "₹9,000", "currency": "INR"},
            {"airline": "Air B", "stops": 0, "price": "₹5,500", "currency": "INR"},
            {"airline": "Air C", "stops": 0, "price": "₹4,200", "currency": "INR"},
        ]))

    monkeypatch.setattr(flight_finder, "generate_with_retry", fake_generate)
    flights = flight_finder._parse_flights_with_gemini(
        "raw page", "DEL", "BOM", "2030-12-01", non_stop=True)
    assert [f["airline"] for f in flights] == ["Air C", "Air B"]
    assert seen["model"] == flight_finder.MODEL_FLASH
    spoken = flight_finder._format_spoken(flights, "DEL", "BOM", "2030-12-01")
    assert "cheapest option is Air C" in spoken


def test_flight_aliases_resolve_iata_codes():
    assert flight_finder._alias("Bengaluru") == "BLR"
    assert flight_finder._alias("guwahati") == "GAU"
    assert flight_finder._alias("IST") == "IST"


# ── friday wake ─────────────────────────────────────────────────────────────
def _make_wav(path: Path, samples, rate=16000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def test_wake_dataset_info_warns_about_silent_clips(tmp_path):
    import numpy as np
    pos, neg = tmp_path / "positive", tmp_path / "negative"
    pos.mkdir()
    neg.mkdir()
    _make_wav(pos / "pos.wav", np.zeros(16000, dtype=np.int16))
    _make_wav(neg / "neg.wav", (np.sin(np.arange(16000) / 10) * 5000).astype(np.int16))
    info = friday_wake.dataset_info(tmp_path)
    assert "1 positive, 1 negative" in info
    assert "Quality warning" in info
    assert len(friday_wake._load_clips(pos)) == 1


def test_wake_clear_dataset_removes_only_clips(tmp_path):
    import numpy as np
    folder = tmp_path / "positive"
    folder.mkdir()
    clip = folder / "pos.wav"
    _make_wav(clip, np.zeros(100, dtype=np.int16))
    result = friday_wake.clear_dataset(tmp_path)
    assert "Removed 1" in result and not clip.exists()


def test_wake_matcher_import_is_lazy():
    assert friday_wake._matcher._svc is None
    source = Path(friday_wake.__file__).read_text(encoding="utf-8")
    assert "best_state" in source and "_load_clips" in source
    assert "google.generativeai" not in source


# ── game updater ────────────────────────────────────────────────────────────
def test_steam_stateflags_bitmask_decoding():
    assert game_updater._steam_state(4).up_to_date
    assert game_updater._steam_state(1026).downloading
    assert game_updater._steam_state(6).update_required
    assert not game_updater._steam_state(6).up_to_date


def test_best_drive_selects_most_free_space(monkeypatch):
    monkeypatch.setattr(game_updater.os.path, "exists", lambda path: path.endswith(":\\"))
    usage = {"C:\\": (100, 20), "D:\\": (1000, 500), "E:\\": (64, 4)}
    monkeypatch.setattr("shutil.disk_usage", lambda path: SimpleNamespace(
        free=usage[str(path)][1] * 1024 ** 3,
        total=usage[str(path)][0] * 1024 ** 3))
    best = game_updater._find_best_drive()
    assert best["letter"] == "D" and "E" not in [best["letter"]]


def test_shutdown_watch_is_cancellable(monkeypatch):
    class FakeEvent:
        def clear(self):
            pass

        def wait(self, seconds):
            return True

    monkeypatch.setattr(game_updater, "_shutdown_cancel", FakeEvent())
    called = []
    monkeypatch.setattr(game_updater.subprocess, "run",
                        lambda *a, **k: called.append(a))
    game_updater._watch_and_shutdown(Path("/nonexistent"), speak=lambda text: None)
    assert called == []


def test_game_actions_launch_largest_close_and_cancel(monkeypatch):
    monkeypatch.setattr(game_updater, "_find_steam_path", lambda: Path("/steam"))
    monkeypatch.setattr(game_updater, "_ensure_steam_running", lambda path: True)
    launches = []
    monkeypatch.setattr(game_updater.subprocess, "Popen",
                        lambda args: launches.append(args))
    monkeypatch.setattr(game_updater, "_search_steam_appid",
                        lambda name: ("730", "CS2"))
    assert "Launching" in game_updater.game_updater({"action": "launch", "game_name": "CS2"})
    assert any("steam://rungameid/730" in arg for args in launches for arg in args)
    monkeypatch.setattr(game_updater, "_get_steam_games", lambda path: [
        {"name": "Big Game", "size": 10 * 1024 ** 3}])
    assert "Big Game" in game_updater.game_updater({"action": "largest"})
    monkeypatch.setattr(game_updater, "_close_game", lambda name: f"closed {name}")
    assert "closed CS2" in game_updater.game_updater({"action": "close_game",
                                                      "game_name": "CS2"})
    assert "stay on" in game_updater.game_updater({"action": "cancel_shutdown"}).lower()


def test_failsafe_is_never_disabled():
    source = Path(game_updater.__file__).read_text(encoding="utf-8")
    assert "pyautogui.FAILSAFE = False" not in source
