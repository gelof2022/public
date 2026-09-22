"""Validated application defaults shared by API and worker through appdata."""
import os
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.config import get_settings


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    detection_threshold: float = Field(default=0.8, ge=0.1, le=0.99)
    recognition_threshold: float = Field(default=0.2, ge=0, le=1)
    grouping_threshold: float = Field(default=0.5, ge=0.1, le=0.5)
    stash_playback_fallback: bool = True
    theme: Literal['dark', 'light'] = 'dark'


def read_preferences(config=None):
    path = (config or get_settings()).appdata_path / 'preferences.json'
    return Preferences.model_validate_json(path.read_text()) if path.exists() else Preferences()


def save_preferences(value, config):
    config.appdata_path.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=config.appdata_path, prefix='.preferences-')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(value.model_dump_json())
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, config.appdata_path / 'preferences.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return value
