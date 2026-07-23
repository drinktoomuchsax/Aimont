"""Direct tests for _is_codex_cli — the process-detection heuristic that
distinguishes the Codex CLI from the unrelated OpenAI Codex desktop app.

Everywhere else this function is mocked, so its logic was untested.
"""

from __future__ import annotations

from aimont import codex_probe
from aimont.codex_probe import _is_codex_cli


class FakeProc:
    def __init__(self, name: str, cmdline: list[str]):
        self._name = name
        self._cmdline = cmdline

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline


def test_matches_plain_cli_invocation():
    assert _is_codex_cli(FakeProc("codex", ["codex", "chat"])) is True
    assert _is_codex_cli(FakeProc("codex.exe", ["C:\\tools\\codex\\bin\\codex.exe"])) is True


def test_rejects_wrong_process_name():
    assert _is_codex_cli(FakeProc("python", ["python", "codex.py"])) is False
    assert _is_codex_cli(FakeProc("node", ["node", "codex"])) is False


def test_rejects_desktop_app_by_chromium_type_flag():
    proc = FakeProc("codex.exe", ["codex.exe", "--type=renderer", "--enable-features=x"])
    assert _is_codex_cli(proc) is False


def test_rejects_desktop_app_by_roaming_path():
    proc = FakeProc("codex.exe", ["C:\\Users\\me\\AppData\\Roaming\\Codex\\codex.exe"])
    assert _is_codex_cli(proc) is False


def test_empty_cmdline_is_not_cli():
    assert _is_codex_cli(FakeProc("codex", [])) is False


def test_dead_process_is_not_cli():
    class DeadProc:
        def name(self):
            raise codex_probe.psutil.NoSuchProcess(123)

        def cmdline(self):
            return []

    assert _is_codex_cli(DeadProc()) is False
