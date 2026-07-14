"""Coding Remote · SDL return model."""
from __future__ import annotations

from imperal_sdk import sdl
from pydantic import model_validator


class CodingRemote(sdl.Entity):
    """Remote-control status of the terminal Webbee Code session.

    active/session_id describe whether a coding session is currently live;
    enabled/mirror/steer are the effective routing state (mirror = where
    session output is echoed, steer = where replies can drive it back)."""
    active: bool | None = None
    session_id: str | None = None
    enabled: bool | None = None
    mirror: list[str] | None = None
    steer: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", "coding-remote")
            data.setdefault("title", "Coding remote")
        return data
