"""Offline unit tests for Windows Administrator auto-elevation in main.py."""

from __future__ import annotations

import ctypes
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as friday_main


class _FakeShellExecuteW:
    def __init__(self, shell32):
        self._shell32 = shell32

    def __call__(self, hwnd, operation, file, parameters, directory, show_cmd):
        self._shell32.calls.append((hwnd, operation, file, parameters, directory, show_cmd))
        return self._shell32.shell_execute_rc


class _FakeShell32:
    def __init__(self, *, is_admin=0, shell_execute_rc=42):
        self.is_admin = is_admin
        self.shell_execute_rc = shell_execute_rc
        self.calls = []
        self.ShellExecuteW = _FakeShellExecuteW(self)

    def IsUserAnAdmin(self):
        return self.is_admin


class TestAdminElevation(unittest.TestCase):
    def test_non_windows_never_elevates(self):
        with patch.object(friday_main.sys, 'platform', 'linux'):
            self.assertFalse(friday_main.is_windows_admin())
            self.assertFalse(friday_main.ensure_admin_if_needed([]))

    def test_already_admin_never_elevates(self):
        shell32 = _FakeShell32(is_admin=1)
        fake_windll = SimpleNamespace(shell32=shell32)
        with patch.object(friday_main.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', fake_windll, create=True), \
             patch.dict(os.environ, {}, clear=True):
            self.assertTrue(friday_main.is_windows_admin())
            self.assertFalse(friday_main.ensure_admin_if_needed([]))
            self.assertEqual(shell32.calls, [])

    def test_no_admin_and_elevated_flags_prevent_elevation(self):
        shell32 = _FakeShell32(is_admin=0)
        fake_windll = SimpleNamespace(shell32=shell32)
        with patch.object(friday_main.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', fake_windll, create=True), \
             patch.dict(os.environ, {}, clear=True):
            self.assertFalse(friday_main.ensure_admin_if_needed(['--no-admin']))
            self.assertFalse(friday_main.ensure_admin_if_needed(['--elevated']))
            self.assertEqual(shell32.calls, [])

    def test_env_guards_prevent_elevation(self):
        shell32 = _FakeShell32(is_admin=0)
        fake_windll = SimpleNamespace(shell32=shell32)
        with patch.object(friday_main.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', fake_windll, create=True):
            with patch.dict(os.environ, {'FRIDAY_NO_ADMIN': '1'}, clear=True):
                self.assertFalse(friday_main.ensure_admin_if_needed([]))
            with patch.dict(os.environ, {'PYTEST_CURRENT_TEST': 'test_x'}, clear=True):
                self.assertFalse(friday_main.ensure_admin_if_needed([]))
            self.assertEqual(shell32.calls, [])

    def test_relaunch_as_admin_calls_shellexecute_with_runas_and_returns_true_on_success(self):
        shell32 = _FakeShell32(is_admin=0, shell_execute_rc=42)
        fake_windll = SimpleNamespace(shell32=shell32)
        with patch.object(friday_main.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', fake_windll, create=True), \
             patch.dict(os.environ, {}, clear=True):
            self.assertTrue(friday_main.ensure_admin_if_needed(['--minimized']))
            self.assertEqual(len(shell32.calls), 1)
            hwnd, op, exe, params, workdir, show_cmd = shell32.calls[0]
            self.assertIsNone(hwnd)
            self.assertEqual(op, 'runas')
            self.assertEqual(exe, sys.executable)
            self.assertIn(str(Path(friday_main.__file__).resolve()), params)
            self.assertIn('--minimized', params)
            self.assertIn('--elevated', params)
            self.assertEqual(workdir, str(Path(friday_main.__file__).resolve().parent))
            self.assertEqual(show_cmd, 1)

    def test_relaunch_as_admin_returns_false_when_uac_declined(self):
        shell32 = _FakeShell32(is_admin=0, shell_execute_rc=5)
        fake_windll = SimpleNamespace(shell32=shell32)
        with patch.object(friday_main.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', fake_windll, create=True), \
             patch.dict(os.environ, {}, clear=True):
            self.assertFalse(friday_main.ensure_admin_if_needed([]))
            self.assertEqual(len(shell32.calls), 1)


if __name__ == '__main__':
    unittest.main()
