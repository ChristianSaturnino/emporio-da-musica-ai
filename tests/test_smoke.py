"""Baseline checks: the package imports, settings resolve and the datasets are in place."""

import subprocess
import sys

from emporio_ai.config import DEFAULT_MODEL, PROJECT_ROOT, get_settings


def test_settings_point_to_project_data_dir():
    assert get_settings().data_dir == PROJECT_ROOT / "data"


def test_model_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "")
    assert get_settings().openai_model == DEFAULT_MODEL


def test_api_key_is_not_exposed_in_repr(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    settings = get_settings()
    assert settings.openai_api_key == "not-a-real-key"
    assert "not-a-real-key" not in repr(settings)


def test_dataset_inventory_passes():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "inspect_data.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
