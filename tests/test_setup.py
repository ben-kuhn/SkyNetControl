import os
import stat
from pathlib import Path

import pytest

from scripts import setup as wizard


def test_load_env_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    assert wizard.load_env(tmp_path / "missing.env") == {}


def test_load_env_parses_key_value_pairs(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("SKYNET_A=one\n# comment\n\nSKYNET_B=two=with=equals\n")
    assert wizard.load_env(p) == {
        "SKYNET_A": "one",
        "SKYNET_B": "two=with=equals",
    }


def test_load_env_ignores_blank_and_comment_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("\n# top comment\nSKYNET_X=1\n  # indented comment\n")
    assert wizard.load_env(p) == {"SKYNET_X": "1"}


def test_save_env_writes_keys_and_sets_0600(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    wizard.save_env({"SKYNET_A": "1", "SKYNET_B": "two"}, p)
    assert p.read_text() == "SKYNET_A=1\nSKYNET_B=two\n"
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_save_env_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    p.write_text("OLD=value\n")
    wizard.save_env({"NEW": "value"}, p)
    assert p.read_text() == "NEW=value\n"
