"""Deep combinatorial tests for memory, safety, api_keys, google_oauth, and logger."""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

if "google.auth.transport.requests" not in sys.modules:
    _g = sys.modules.setdefault("google", types.ModuleType("google"))
    _ga = sys.modules.setdefault("google.auth", types.ModuleType("google.auth"))
    _gat = sys.modules.setdefault("google.auth.transport", types.ModuleType("google.auth.transport"))
    _gatr = sys.modules.setdefault("google.auth.transport.requests", types.ModuleType("google.auth.transport.requests"))
    _go = sys.modules.setdefault("google.oauth2", types.ModuleType("google.oauth2"))
    _goc = sys.modules.setdefault("google.oauth2.credentials", types.ModuleType("google.oauth2.credentials"))
    _goc.Credentials = MagicMock

import api_keys
import google_oauth
import logger
import safety
from memory import memory_manager


# ---------------------------------------------------------------------------
# memory/memory_manager.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cat", ["identity", "preferences", "projects", "relationships", "wishes", "notes"])
def test_memory_update_and_load_all_categories(tmp_path, monkeypatch, cat):
    mem_file = tmp_path / "long_term.json"
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", mem_file)
    res = memory_manager.update_memory({cat: {"item": "value"}})
    assert res[cat]["item"]["value"] == "value"
    loaded = memory_manager.load_memory()
    assert loaded[cat]["item"]["value"] == "value"


@pytest.mark.parametrize("key,val", [
    ("api_key", "anything"),
    ("password", "secret123"),
    ("user_token", "abc"),
    ("notes", "AIzaSyDummyKey123456789012345678901234567"),
    ("notes", "sk-1234567890abcdefghij1234567890"),
    ("notes", "ghp_1234567890abcdefghij1234567890"),
    ("notes", {"value": "AIzaSyDummyKey123456789012345678901234567"}),
])
def test_memory_secret_filtering_blocks_secrets(tmp_path, monkeypatch, key, val):
    mem_file = tmp_path / "long_term.json"
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", mem_file)
    res = memory_manager.update_memory({"notes": {key: val}})
    assert key not in res.get("notes", {})


def test_memory_non_dict_root_and_corrupt_json(tmp_path, monkeypatch):
    mem_file = tmp_path / "long_term.json"
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", mem_file)
    mem_file.write_text("[1, 2, 3]", encoding="utf-8")
    loaded = memory_manager.load_memory()
    assert isinstance(loaded, dict) and "identity" in loaded

    mem_file.write_text("{corrupt", encoding="utf-8")
    loaded2 = memory_manager.load_memory()
    assert isinstance(loaded2, dict) and "identity" in loaded2


def test_memory_prune_handles_missing_updated():
    mem = {"notes": {f"k{i}": {"value": f"v{i}"} if i % 2 == 0 else "raw" for i in range(50)}}
    memory_manager.prune_memory(mem, cap=10)
    assert len(mem["notes"]) == 10


def test_memory_concurrent_updates(tmp_path, monkeypatch):
    mem_file = tmp_path / "long_term.json"
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", mem_file)
    errors = []

    def _worker(idx: int):
        try:
            memory_manager.update_memory({"notes": {f"k{idx}": f"v{idx}"}})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# ---------------------------------------------------------------------------
# safety.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args,expected_risk", [
    ("cmd_control", {"command": "dir"}, safety.Risk.HIGH),
    ("desktop_control", {"action": "run"}, safety.Risk.HIGH),
    ("clear_memory", {}, safety.Risk.HIGH),
    ("forget_memory", {"category": "notes", "key": "x"}, safety.Risk.MEDIUM),
    ("file_controller", {"action": "delete", "path": "a.txt"}, safety.Risk.HIGH),
    ("file_controller", {"action": "read", "path": "a.txt"}, safety.Risk.MEDIUM),
    ("gmail", {"action": "send", "to": "a@b.com"}, safety.Risk.HIGH),
    ("gmail", {"action": "read"}, safety.Risk.LOW),
    ("task_manager", {"action": "kill", "task_id": "1"}, safety.Risk.HIGH),
    ("screen_process", {}, safety.Risk.LOW),
])
def test_safety_classify_matrix(tool, args, expected_risk):
    assert safety.classify(tool, args) == expected_risk


@pytest.mark.parametrize("tool,args,expected_substr", [
    ("cmd_control", {"command": "echo hi"}, "echo hi"),
    ("cmd_control", {"task": "list files"}, "list files"),
    ("desktop_control", {"action": "clean"}, "clean"),
    ("desktop_control", {"task": "organize"}, "organize"),
    ("game_updater", {"game_name": "Portal"}, "Portal"),
    ("task_manager", {"action": "kill", "task_id": "42"}, "42"),
])
def test_safety_describe_uses_real_param_names(tool, args, expected_substr):
    desc = safety.describe(tool, args)
    assert expected_substr in desc


def test_safety_token_issue_and_redeem_thread_safe():
    tok = safety.issue_confirmation_token("cmd_control", {"command": "dir"})
    assert tok
    assert safety.redeem_confirmation_token("cmd_control", {"command": "dir"}, tok) is True
    # Replay must fail
    assert safety.redeem_confirmation_token("cmd_control", {"command": "dir"}, tok) is False


# ---------------------------------------------------------------------------
# api_keys.py
# ---------------------------------------------------------------------------

def test_api_keys_rotation_and_cooldown(tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_keys": ["AIzaKey1", "AIzaKey2", "AIzaKey3"]}), encoding="utf-8")
    monkeypatch.setattr(api_keys, "CONFIG_PATH", cfg)

    k1 = api_keys.next_key()
    assert k1 in {"AIzaKey1", "AIzaKey2", "AIzaKey3"}
    api_keys.mark_exhausted(k1)
    k2 = api_keys.next_key()
    assert k2 != k1
    api_keys.mark_exhausted(k2)
    k3 = api_keys.next_key()
    assert k3 not in (k1, k2)


# ---------------------------------------------------------------------------
# google_oauth.py
# ---------------------------------------------------------------------------

def test_google_oauth_save_token_resets_cache(tmp_path, monkeypatch):
    tok_path = tmp_path / "google_token.json"
    monkeypatch.setattr(google_oauth, "TOKEN_PATH", tok_path)
    google_oauth._cached = "stale-creds"
    google_oauth._save_token({"refresh_token": "rt-123"})
    assert google_oauth._cached is None
    assert google_oauth._load_token()["refresh_token"] == "rt-123"


def test_google_oauth_corrupt_token_returns_empty(tmp_path, monkeypatch):
    tok_path = tmp_path / "google_token.json"
    tok_path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(google_oauth, "TOKEN_PATH", tok_path)
    assert google_oauth._load_token() == {}


# ---------------------------------------------------------------------------
# logger.py
# ---------------------------------------------------------------------------

def test_logger_log_tool_redacts_nested_secrets(tmp_path, monkeypatch):
    log_file = tmp_path / "friday.log"
    monkeypatch.setattr(logger, "LOG_PATH", log_file, raising=False)
    logger.log_tool("gmail", {"api_key": "secret123", "body": "hello"}, result="ok")
