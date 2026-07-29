import asyncio
import argparse
import base64
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable
from urllib.parse import quote, urlparse

import mss
import mss.tools
import pyautogui
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
import webview


pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENSION_FILE = os.path.join(HERE, "scratchlink_penguinmod.js")
TOOLS_DIR = os.path.join(HERE, "tools")
UI_DIR = os.path.join(HERE, "ui")
CLOUDFLARED_FALLBACK_PATH = os.path.join(TOOLS_DIR, "cloudflared.exe")
CLOUDFLARED_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_AI_MODEL = os.environ.get("SCRATCHLINK_AI_MODEL", "openrouter/free")
DEFAULT_AI_ENDPOINT = os.environ.get("SCRATCHLINK_AI_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
DEFAULT_AI_TOKEN_ENV = "SCRATCHLINK_OPENROUTER_KEY"
DEFAULT_AI_BACKUP_TOKEN_ENV = "SCRATCHLINK_OPENROUTER_BACKUP_KEY"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    with suppress(ValueError):
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return value


def shorten_connection_id(value: str) -> str:
    if len(value) <= 18:
        return value
    return f"{value[:8]}...{value[-6:]}"


@dataclass
class ConnectionRecord:
    id: str
    name: str
    password: str
    hf_token: str
    enabled: bool
    created_at: str
    last_used_at: str | None = None


class ConnectionStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._connections: dict[str, ConnectionRecord] = {}

    def _sorted_connections_locked(self) -> list[ConnectionRecord]:
        return sorted(self._connections.values(), key=lambda item: (item.created_at, item.name.casefold()))

    def list_connections(self) -> list[ConnectionRecord]:
        with self._lock:
            return [ConnectionRecord(**asdict(item)) for item in self._sorted_connections_locked()]

    def count(self) -> int:
        with self._lock:
            return len(self._connections)

    def _default_name_locked(self) -> str:
        existing_names = {item.name.casefold() for item in self._connections.values()}
        index = 1
        while True:
            candidate = f"Connection {index}"
            if candidate.casefold() not in existing_names:
                return candidate
            index += 1

    def create_connection(self, name: str | None = None, hf_token: str | None = None) -> ConnectionRecord:
        with self._lock:
            connection = ConnectionRecord(
                id=uuid.uuid4().hex,
                name=(name or "").strip() or self._default_name_locked(),
                password=secrets.token_urlsafe(18),
                hf_token=str(hf_token or "").strip(),
                enabled=True,
                created_at=now_iso(),
            )
            self._connections[connection.id] = connection
            return ConnectionRecord(**asdict(connection))

    def get(self, connection_id: str) -> ConnectionRecord | None:
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return None
            return ConnectionRecord(**asdict(connection))

    def require(self, connection_id: str) -> ConnectionRecord:
        connection = self.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Unknown ScratchLink connection")
        return connection

    def rename_connection(self, connection_id: str, new_name: str) -> ConnectionRecord:
        cleaned = new_name.strip()
        if not cleaned:
            raise ValueError("Connection name cannot be empty")

        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise KeyError(connection_id)
            connection.name = cleaned
            return ConnectionRecord(**asdict(connection))

    def set_hf_token(self, connection_id: str, hf_token: str) -> ConnectionRecord:
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise KeyError(connection_id)
            connection.hf_token = str(hf_token or "").strip()
            return ConnectionRecord(**asdict(connection))

    def toggle_connection(self, connection_id: str) -> ConnectionRecord:
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise KeyError(connection_id)
            connection.enabled = not connection.enabled
            return ConnectionRecord(**asdict(connection))

    def regenerate_password(self, connection_id: str) -> ConnectionRecord:
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise KeyError(connection_id)
            connection.password = secrets.token_urlsafe(18)
            return ConnectionRecord(**asdict(connection))

    def delete_connection(self, connection_id: str) -> None:
        with self._lock:
            if connection_id not in self._connections:
                raise KeyError(connection_id)
            del self._connections[connection_id]

    def authenticate(self, connection_id: str | None, password: str | None) -> ConnectionRecord:
        if not connection_id or not password:
            raise HTTPException(status_code=403, detail="ScratchLink credentials are required")

        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise HTTPException(status_code=403, detail="Unknown ScratchLink connection")
            if not connection.enabled:
                raise HTTPException(status_code=403, detail="This ScratchLink connection is turned off")
            if not secrets.compare_digest(connection.password, password):
                raise HTTPException(status_code=403, detail="ScratchLink password was rejected")
            connection.last_used_at = now_iso()
            return ConnectionRecord(**asdict(connection))


@dataclass
class HostedRequestRecord:
    request_id: str
    connection_id: str
    directory_name: str
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body_text: str
    received_at: str
    event: threading.Event
    response_ready: bool = False
    status_code: int = 200
    response_headers: dict[str, str] | None = None
    response_body: str = ""


@dataclass
class HostedDirectoryRecord:
    name: str
    connection_id: str
    created_at: str
    open: bool = True
    requests: dict[str, HostedRequestRecord] | None = None
    request_order: list[str] | None = None

    def __post_init__(self) -> None:
        if self.requests is None:
            self.requests = {}
        if self.request_order is None:
            self.request_order = []


class DirectoryHost:
    def __init__(self):
        self._lock = threading.RLock()
        self._directories: dict[str, HostedDirectoryRecord] = {}
        self._request_index: dict[str, HostedRequestRecord] = {}

    def _normalize_name(self, value: str) -> str:
        cleaned = "".join(character for character in str(value or "").strip().lower() if character.isalnum() or character in ("-", "_"))
        if not cleaned:
            raise HTTPException(status_code=400, detail="Directory name must contain letters or numbers")
        return cleaned

    def open_directory(self, connection_id: str, name: str) -> HostedDirectoryRecord:
        directory_name = self._normalize_name(name)
        with self._lock:
            existing = self._directories.get(directory_name)
            if existing is not None and existing.connection_id != connection_id:
                raise HTTPException(status_code=409, detail="That hosted directory name is already in use")
            if existing is None:
                existing = HostedDirectoryRecord(name=directory_name, connection_id=connection_id, created_at=now_iso(), open=True)
                self._directories[directory_name] = existing
            else:
                existing.open = True
            return existing

    def close_directory(self, connection_id: str, name: str) -> HostedDirectoryRecord:
        directory_name = self._normalize_name(name)
        with self._lock:
            directory = self._directories.get(directory_name)
            if directory is None or directory.connection_id != connection_id:
                raise HTTPException(status_code=404, detail="That hosted directory was not found")
            directory.open = False
            return directory

    def get_directory(self, connection_id: str, name: str) -> HostedDirectoryRecord:
        directory_name = self._normalize_name(name)
        with self._lock:
            directory = self._directories.get(directory_name)
            if directory is None or directory.connection_id != connection_id:
                raise HTTPException(status_code=404, detail="That hosted directory was not found")
            return directory

    def queue_request(self, directory_name: str, method: str, path: str, query: str, headers: dict[str, str], body_text: str) -> HostedRequestRecord:
        normalized_name = self._normalize_name(directory_name)
        with self._lock:
            directory = self._directories.get(normalized_name)
            if directory is None or not directory.open:
                raise HTTPException(status_code=404, detail="That hosted directory is not open")

            request_id = uuid.uuid4().hex
            record = HostedRequestRecord(
                request_id=request_id,
                connection_id=directory.connection_id,
                directory_name=normalized_name,
                method=method.upper(),
                path=path,
                query=query,
                headers=headers,
                body_text=body_text,
                received_at=now_iso(),
                event=threading.Event(),
            )
            directory.requests[request_id] = record
            directory.request_order.append(request_id)
            self._request_index[request_id] = record
            return record

    def list_waiting_requests(self, connection_id: str, name: str) -> list[HostedRequestRecord]:
        directory = self.get_directory(connection_id, name)
        with self._lock:
            return [
                directory.requests[request_id]
                for request_id in directory.request_order
                if request_id in directory.requests and not directory.requests[request_id].response_ready
            ]

    def respond_to_request(
        self,
        connection_id: str,
        request_id: str,
        status_code: int,
        headers: dict[str, str],
        body_text: str,
    ) -> HostedRequestRecord:
        with self._lock:
            record = self._request_index.get(request_id)
            if record is None or record.connection_id != connection_id:
                raise HTTPException(status_code=404, detail="That hosted request was not found")
            if record.response_ready:
                raise HTTPException(status_code=409, detail="That hosted request already has a response")
            record.status_code = max(100, min(int(status_code), 599))
            record.response_headers = headers
            record.response_body = body_text
            record.response_ready = True
            record.event.set()
            return record

    def finish_request(self, request_id: str) -> None:
        with self._lock:
            record = self._request_index.pop(request_id, None)
            if record is None:
                return
            directory = self._directories.get(record.directory_name)
            if directory is None:
                return
            directory.requests.pop(request_id, None)
            with suppress(ValueError):
                directory.request_order.remove(request_id)


def serialize_hosted_request(record: HostedRequestRecord) -> dict[str, Any]:
    return {
        "requestId": record.request_id,
        "directory": record.directory_name,
        "method": record.method,
        "path": record.path,
        "query": record.query,
        "headers": record.headers,
        "body": record.body_text,
        "receivedAt": record.received_at,
    }


def parse_headers_json(value: str) -> dict[str, str]:
    text = str(value or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Headers must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Headers must be a JSON object")
    return {str(key): str(item) for key, item in parsed.items()}


def perform_outbound_http_request(method: str, url: str, headers: dict[str, str], body: str = "") -> dict[str, Any]:
    cleaned_method = str(method or "").strip().upper()
    cleaned_url = str(url or "").strip()
    if cleaned_method not in {"GET", "POST"}:
        raise HTTPException(status_code=400, detail="Only GET and POST requests are supported")
    if not cleaned_url:
        raise HTTPException(status_code=400, detail="A URL is required")

    parsed = urlparse(cleaned_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    payload = None
    outbound_headers = dict(headers)
    if cleaned_method == "POST":
        payload = str(body or "").encode("utf-8")
        if not any(key.lower() == "content-type" for key in outbound_headers):
            outbound_headers["Content-Type"] = "text/plain; charset=utf-8"

    request = urllib.request.Request(cleaned_url, data=payload, headers=outbound_headers, method=cleaned_method)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(response.status),
                "headers": {str(key): str(value) for key, value in response.headers.items()},
                "body": response_body,
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": True,
            "status": int(exc.code),
            "headers": {str(key): str(value) for key, value in exc.headers.items()},
            "body": response_body,
        }
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"ScratchLink could not reach that URL: {exc.reason}") from exc


@dataclass
class ScreenObjectRecord:
    kind: str
    object_id: str
    x: int
    y: int
    width: int = 0
    height: int = 0
    text: str = ""
    background: str = "#ffffff"
    color: str = "#17324d"
    font_size: int = 18


@dataclass
class ScreenAnalyticRecord:
    analytic_id: str
    name: str
    value: str
    kind: str = "value"


@dataclass
class ScreenStateRecord:
    mode: str = "objects"
    width: int = 64
    height: int = 64
    objects: list[ScreenObjectRecord] | None = None
    analytics: list[ScreenAnalyticRecord] | None = None
    pixels: dict[str, str] | None = None
    image_data_uri: str = ""
    pressed_button_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.objects is None:
            self.objects = []
        if self.analytics is None:
            self.analytics = []
        if self.pixels is None:
            self.pixels = {}
        if self.pressed_button_ids is None:
            self.pressed_button_ids = []


class ScreenManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._screens: dict[str, ScreenStateRecord] = {}

    @staticmethod
    def _normalize_analytic_kind(kind: str) -> str:
        cleaned = str(kind or "").strip().lower()
        return "progress" if cleaned == "progress" else "value"

    @staticmethod
    def _normalize_progress_value(value: str) -> str:
        try:
            number = float(str(value or "0").strip() or "0")
        except ValueError:
            number = 0.0
        clamped = min(100.0, max(0.0, number))
        if clamped.is_integer():
            return str(int(clamped))
        return f"{clamped:.2f}".rstrip("0").rstrip(".")

    def _get_or_create_locked(self, connection_id: str) -> ScreenStateRecord:
        state = self._screens.get(connection_id)
        if state is None:
            state = ScreenStateRecord()
            self._screens[connection_id] = state
        return state

    def get_state(self, connection_id: str) -> ScreenStateRecord:
        with self._lock:
            return self._get_or_create_locked(connection_id)

    def clear_screen(self, connection_id: str) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            state.objects.clear()
            state.analytics.clear()
            state.pixels.clear()
            state.image_data_uri = ""
            state.pressed_button_ids.clear()
            return state

    def set_mode(self, connection_id: str, mode: str) -> ScreenStateRecord:
        cleaned = str(mode or "").strip().lower()
        if cleaned not in {"objects", "pixels", "image", "analytics"}:
            raise HTTPException(status_code=400, detail="Screen mode must be objects, analytics, or pixels/image")
        normalized = "pixels" if cleaned in {"pixels", "image"} else cleaned
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            state.mode = normalized
            state.objects.clear()
            state.analytics.clear()
            state.pixels.clear()
            state.image_data_uri = ""
            state.pressed_button_ids.clear()
            return state

    def add_button(
        self,
        connection_id: str,
        object_id: str,
        text: str,
        x: int,
        y: int,
        width: int,
        height: int,
        background: str,
        color: str,
    ) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            state.objects.append(
                ScreenObjectRecord(
                    kind="button",
                    object_id=str(object_id or "").strip() or uuid.uuid4().hex,
                    text=str(text or ""),
                    x=int(x),
                    y=int(y),
                    width=max(int(width), 1),
                    height=max(int(height), 1),
                    background=str(background or "#ffffff"),
                    color=str(color or "#17324d"),
                )
            )
            return state

    def add_text(
        self,
        connection_id: str,
        object_id: str,
        text: str,
        x: int,
        y: int,
        color: str,
        font_size: int,
    ) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            state.objects.append(
                ScreenObjectRecord(
                    kind="text",
                    object_id=str(object_id or "").strip() or uuid.uuid4().hex,
                    text=str(text or ""),
                    x=int(x),
                    y=int(y),
                    color=str(color or "#17324d"),
                    font_size=max(int(font_size), 1),
                )
            )
            return state

    def update_text(self, connection_id: str, object_id: str, text: str) -> ScreenStateRecord:
        target_id = str(object_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="A text object id is required")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            for item in state.objects:
                if item.kind == "text" and item.object_id == target_id:
                    item.text = str(text or "")
                    return state
        raise HTTPException(status_code=404, detail="That text object was not found")

    def update_button_text(self, connection_id: str, object_id: str, text: str) -> ScreenStateRecord:
        target_id = str(object_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="A button object id is required")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            for item in state.objects:
                if item.kind == "button" and item.object_id == target_id:
                    item.text = str(text or "")
                    return state
        raise HTTPException(status_code=404, detail="That button object was not found")

    def add_box(
        self,
        connection_id: str,
        object_id: str,
        x: int,
        y: int,
        width: int,
        height: int,
        background: str,
    ) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            state.objects.append(
                ScreenObjectRecord(
                    kind="box",
                    object_id=str(object_id or "").strip() or uuid.uuid4().hex,
                    x=int(x),
                    y=int(y),
                    width=max(int(width), 1),
                    height=max(int(height), 1),
                    background=str(background or "#cccccc"),
                )
            )
            return state

    def remove_object(self, connection_id: str, object_id: str) -> ScreenStateRecord:
        target_id = str(object_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="An object id is required")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "objects":
                raise HTTPException(status_code=409, detail="Screen must be in objects mode")
            remaining = [item for item in state.objects if item.object_id != target_id]
            if len(remaining) == len(state.objects):
                raise HTTPException(status_code=404, detail="That object was not found")
            state.objects = remaining
            return state

    def add_analytic(self, connection_id: str, analytic_id: str, name: str, value: str, kind: str) -> ScreenStateRecord:
        target_id = str(analytic_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="An analytic id is required")
        normalized_kind = self._normalize_analytic_kind(kind)
        normalized_value = self._normalize_progress_value(value) if normalized_kind == "progress" else str(value or "")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "analytics":
                raise HTTPException(status_code=409, detail="Screen must be in analytics mode")
            for item in state.analytics:
                if item.analytic_id == target_id:
                    item.kind = normalized_kind
                    item.name = str(name or item.name or target_id)
                    item.value = normalized_value
                    return state
            state.analytics.append(
                ScreenAnalyticRecord(
                    analytic_id=target_id,
                    name=str(name or target_id),
                    kind=normalized_kind,
                    value=normalized_value,
                )
            )
            return state

    def update_analytic_value(self, connection_id: str, analytic_id: str, value: str) -> ScreenStateRecord:
        target_id = str(analytic_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="An analytic id is required")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "analytics":
                raise HTTPException(status_code=409, detail="Screen must be in analytics mode")
            for item in state.analytics:
                if item.analytic_id == target_id:
                    item.value = self._normalize_progress_value(value) if item.kind == "progress" else str(value or "")
                    return state
        raise HTTPException(status_code=404, detail="That analytic was not found")

    def remove_analytic(self, connection_id: str, analytic_id: str) -> ScreenStateRecord:
        target_id = str(analytic_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="An analytic id is required")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "analytics":
                raise HTTPException(status_code=409, detail="Screen must be in analytics mode")
            remaining = [item for item in state.analytics if item.analytic_id != target_id]
            if len(remaining) == len(state.analytics):
                raise HTTPException(status_code=404, detail="That analytic was not found")
            state.analytics = remaining
            return state

    def set_resolution(self, connection_id: str, width: int, height: int) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "pixels":
                raise HTTPException(status_code=409, detail="Screen must be in pixels/image mode")
            state.width = max(int(width), 1)
            state.height = max(int(height), 1)
            state.pixels.clear()
            state.image_data_uri = ""
            return state

    def set_pixel(self, connection_id: str, x: int, y: int, color: str) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "pixels":
                raise HTTPException(status_code=409, detail="Screen must be in pixels/image mode")
            pixel_x = int(x)
            pixel_y = int(y)
            if pixel_x < 0 or pixel_y < 0 or pixel_x >= state.width or pixel_y >= state.height:
                raise HTTPException(status_code=400, detail="Pixel is outside the current screen resolution")
            state.pixels[f"{pixel_x},{pixel_y}"] = str(color or "#000000")
            state.image_data_uri = ""
            return state

    def set_image_data_uri(self, connection_id: str, image_data_uri: str) -> ScreenStateRecord:
        cleaned = str(image_data_uri or "").strip()
        if not cleaned.startswith("data:image/png"):
            raise HTTPException(status_code=400, detail="Screen image must be a PNG data URI")
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            if state.mode != "pixels":
                raise HTTPException(status_code=409, detail="Screen must be in pixels/image mode")
            state.pixels.clear()
            state.image_data_uri = cleaned
            return state

    def record_button_press(self, connection_id: str, object_id: str) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            state.pressed_button_ids.append(str(object_id or ""))
            return state

    def clear_pressed_buttons(self, connection_id: str) -> ScreenStateRecord:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            state.pressed_button_ids.clear()
            return state

    def serialize(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._get_or_create_locked(connection_id)
            return {
                "mode": state.mode,
                "width": state.width,
                "height": state.height,
                "objects": [
                    {
                        "kind": item.kind,
                        "id": item.object_id,
                        "x": item.x,
                        "y": item.y,
                        "width": item.width,
                        "height": item.height,
                        "text": item.text,
                        "background": item.background,
                        "color": item.color,
                        "fontSize": item.font_size,
                    }
                    for item in state.objects
                ],
                "analytics": [
                    {
                        "id": item.analytic_id,
                        "name": item.name,
                        "kind": item.kind,
                        "value": item.value,
                    }
                    for item in state.analytics
                ],
                "pixels": state.pixels,
                "imageDataUri": state.image_data_uri,
                "pressedButtonIds": list(state.pressed_button_ids),
            }


class HostedAiManager:
    def __init__(self, model_id: str = DEFAULT_AI_MODEL, endpoint: str = DEFAULT_AI_ENDPOINT):
        self.model_id = model_id
        self.endpoint = endpoint
        self._lock = threading.RLock()

    def _resolve_tokens(self, connection: ConnectionRecord, primary_key: str = "", backup_key: str = "") -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        for candidate in (
            str(primary_key or "").strip(),
            str(backup_key or "").strip(),
            str(connection.hf_token or "").strip(),
            os.environ.get(DEFAULT_AI_TOKEN_ENV, "").strip(),
            os.environ.get(DEFAULT_AI_BACKUP_TOKEN_ENV, "").strip(),
        ):
            if candidate and candidate not in seen:
                ordered.append(candidate)
                seen.add(candidate)

        if ordered:
            return ordered

        raise HTTPException(
            status_code=503,
            detail=(
                "ScratchLink AI is not configured yet. "
                "Pass an OpenRouter API key into the AI block or set "
                f"{DEFAULT_AI_TOKEN_ENV} and optionally {DEFAULT_AI_BACKUP_TOKEN_ENV} before starting the app."
            ),
        )

    def _extract_error_detail(self, body: str) -> str:
        detail = "ScratchLink AI request failed."
        with suppress(json.JSONDecodeError):
            error_payload = json.loads(body)
            if isinstance(error_payload, dict):
                error_block = error_payload.get("error")
                if isinstance(error_block, dict) and error_block.get("message"):
                    detail = str(error_block["message"])
                elif error_payload.get("message"):
                    detail = str(error_payload["message"])
        return detail

    def _should_try_backup_key(self, status_code: int, body: str) -> bool:
        if status_code in {402, 429}:
            return True

        with suppress(json.JSONDecodeError):
            error_payload = json.loads(body)
            if isinstance(error_payload, dict):
                error_block = error_payload.get("error")
                if isinstance(error_block, dict):
                    metadata = error_block.get("metadata")
                    if isinstance(metadata, dict):
                        error_type = str(metadata.get("error_type", "")).strip().lower()
                        if error_type in {"payment_required", "rate_limit_exceeded", "token_limit_exceeded"}:
                            return True
        return False

    def _perform_request(self, token: str, request_bytes: bytes) -> str:
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(
            self.endpoint,
            data=request_bytes,
            headers=request_headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")

    def _extract_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                return "".join(text_parts).strip()

        if isinstance(payload.get("generated_text"), str):
            return str(payload["generated_text"]).strip()

        return ""

    def generate(
        self,
        connection: ConnectionRecord,
        prompt: str,
        instructions: str = "",
        json_format: str = "",
        api_key: str = "",
        backup_api_key: str = "",
    ) -> dict[str, Any]:
        cleaned_prompt = str(prompt or "").strip()
        if not cleaned_prompt:
            raise HTTPException(status_code=400, detail="An AI prompt is required")

        cleaned_instructions = str(instructions or "").strip()
        cleaned_json_format = str(json_format or "").strip()

        system_parts = []
        if cleaned_instructions:
            system_parts.append(cleaned_instructions)
        else:
            system_parts.append("Be helpful, concise, and follow the user's instructions carefully.")
        if cleaned_json_format:
            system_parts.append(
                "Respond with only a valid JSON object and no extra text. "
                f"The JSON must match this format: {cleaned_json_format}"
            )

        request_payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": "\n".join(system_parts)},
                {"role": "user", "content": cleaned_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
            "stream": False,
        }

        request_bytes = json.dumps(request_payload).encode("utf-8")
        tokens = self._resolve_tokens(connection, api_key, backup_api_key)

        with self._lock:
            last_http_error: urllib.error.HTTPError | None = None
            last_error_detail = "ScratchLink AI request failed."
            raw_body = ""
            for index, token in enumerate(tokens):
                try:
                    raw_body = self._perform_request(token, request_bytes)
                    break
                except urllib.error.HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    last_http_error = exc
                    last_error_detail = self._extract_error_detail(body)
                    should_retry = index < len(tokens) - 1 and self._should_try_backup_key(exc.code, body)
                    if should_retry:
                        continue
                    raise HTTPException(status_code=502, detail=f"ScratchLink AI request failed: {last_error_detail}") from exc
                except urllib.error.URLError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail="ScratchLink could not reach OpenRouter right now.",
                    ) from exc
            else:
                raise HTTPException(
                    status_code=502,
                    detail=f"ScratchLink AI request failed: {last_error_detail}",
                ) from last_http_error

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="ScratchLink AI returned an invalid response.") from exc

        response_text = self._extract_text(payload)
        if not response_text:
            raise HTTPException(status_code=502, detail="ScratchLink AI returned an empty response.")

        return {
            "ok": True,
            "model": self.model_id,
            "text": response_text,
        }


class RuntimeState:
    def __init__(self):
        self.store: ConnectionStore | None = None
        self.extension_template = ""
        self.local_base_url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
        self.public_base_url = ""
        self.admin_token = secrets.token_urlsafe(24)
        self.directory_host = DirectoryHost()
        self.screen_manager = ScreenManager()
        self.ai_manager = HostedAiManager()

    def require_store(self) -> ConnectionStore:
        if self.store is None:
            raise HTTPException(status_code=503, detail="ScratchLink is not ready yet")
        return self.store

    def api_base_url(self) -> str:
        return self.public_base_url or self.local_base_url


STATE = RuntimeState()


class MouseMoveRequest(BaseModel):
    x: int
    y: int
    duration: float = 0


class MouseOffsetRequest(BaseModel):
    dx: int
    dy: int
    duration: float = 0


class MouseButtonRequest(BaseModel):
    button: str = "left"


class MouseClickRequest(BaseModel):
    button: str = "left"
    clicks: int = 1
    interval: float = 0


class KeyRequest(BaseModel):
    key: str


class HotkeyRequest(BaseModel):
    keys: list[str] = Field(default_factory=list)


class WriteRequest(BaseModel):
    text: str
    interval: float = 0


class WaitRequest(BaseModel):
    seconds: float = 0.1


class RobloxGameRequest(BaseModel):
    id: str


class OpenFileRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    text: str


class FolderRequest(BaseModel):
    path: str


class OpenAppRequest(BaseModel):
    name: str


class ActionItem(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BatchRequest(BaseModel):
    actions: list[ActionItem] = Field(default_factory=list)


class ConnectionCreateRequest(BaseModel):
    name: str | None = None
    hf_token: str = ""


class ConnectionRenameRequest(BaseModel):
    name: str


class ConnectionTokenRequest(BaseModel):
    hf_token: str = ""


class HostedDirectoryRequest(BaseModel):
    name: str


class HostedRequestResponsePayload(BaseModel):
    request_id: str
    status: int = 200
    headers: str = "{}"
    body: str = ""


class OutboundHttpRequest(BaseModel):
    url: str
    headers: str = "{}"
    body: str = ""


class AiGenerateRequest(BaseModel):
    prompt: str
    instructions: str = ""
    api_key: str = ""
    backup_api_key: str = ""
    json_format: str = ""


class ScreenModeRequest(BaseModel):
    mode: str


class ScreenResolutionRequest(BaseModel):
    width: int
    height: int


class ScreenPixelRequest(BaseModel):
    x: int
    y: int
    color: str = "#000000"


class ScreenImageRequest(BaseModel):
    data_uri: str


class ScreenAnalyticCreateRequest(BaseModel):
    object_id: str
    name: str
    value: str
    kind: str = "value"


class ScreenAnalyticUpdateRequest(BaseModel):
    object_id: str
    value: str


class ScreenItemRemoveRequest(BaseModel):
    object_id: str


class ScreenButtonRequest(BaseModel):
    object_id: str
    text: str
    x: int
    y: int
    width: int = 120
    height: int = 40
    background: str = "#ffffff"
    color: str = "#17324d"


class ScreenTextRequest(BaseModel):
    object_id: str
    text: str
    x: int
    y: int
    color: str = "#17324d"
    font_size: int = 18


class ScreenTextUpdateRequest(BaseModel):
    object_id: str
    text: str


class ScreenButtonTextUpdateRequest(BaseModel):
    object_id: str
    text: str


class ScreenBoxRequest(BaseModel):
    object_id: str
    x: int
    y: int
    width: int
    height: int
    background: str = "#cccccc"


app = FastAPI(title="ScratchLink")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MOUSE_BUTTONS = {"left", "middle", "right"}
KEY_ALIASES = {
    "windows": "win",
    "meta": "win",
    "super": "win",
    "command": "command",
    "cmd": "command",
}


def load_extension_template() -> str:
    with open(EXTENSION_FILE, "r", encoding="utf-8") as handle:
        return handle.read()


def build_extension_script(connection: ConnectionRecord) -> str:
    base_url = STATE.api_base_url().rstrip("/")
    extension_url = make_extension_url(connection, base_url)
    return (
        STATE.extension_template
        .replace("__SCRATCHLINK_BASE_URL__", base_url)
        .replace("__SCRATCHLINK_CONNECTION_ID__", connection.id)
        .replace("__SCRATCHLINK_PASSWORD__", connection.password)
        .replace("__SCRATCHLINK_EXTENSION_URL__", extension_url)
    )


def make_extension_url(connection: ConnectionRecord, base_url: str | None = None) -> str:
    base = (base_url or STATE.api_base_url()).rstrip("/")
    return f"{base}/extension/{connection.id}.js?password={quote(connection.password)}"


def get_store() -> ConnectionStore:
    return STATE.require_store()


def require_connection(
    x_scratchlink_connection: str | None = Header(default=None),
    x_scratchlink_password: str | None = Header(default=None),
) -> ConnectionRecord:
    return get_store().authenticate(x_scratchlink_connection, x_scratchlink_password)


def require_admin(
    request: Request,
    x_scratchlink_admin: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    client_host = ""
    if request.client and request.client.host:
        client_host = request.client.host.strip().lower()

    if client_host in {"127.0.0.1", "localhost", "::1"}:
        return

    supplied = x_scratchlink_admin or token
    if not supplied or not secrets.compare_digest(supplied, STATE.admin_token):
        raise HTTPException(status_code=403, detail="ScratchLink admin token was rejected")


def serialize_connection(connection: ConnectionRecord) -> dict[str, Any]:
    return {
        "id": connection.id,
        "name": connection.name,
        "password": connection.password,
        "hfToken": connection.hf_token,
        "hasHfToken": bool(str(connection.hf_token or "").strip()),
        "enabled": connection.enabled,
        "createdAt": connection.created_at,
        "lastUsedAt": connection.last_used_at,
        "extensionUrl": make_extension_url(connection),
    }


def normalize_button(button: str) -> str:
    button = button.lower().strip()
    if button not in MOUSE_BUTTONS:
        raise HTTPException(status_code=400, detail=f"Unsupported mouse button: {button}")
    return button


def normalize_key(key: str) -> str:
    return KEY_ALIASES.get(key.lower().strip(), key.lower().strip())


def capture_monitor(monitor: dict[str, int]) -> dict[str, Any]:
    with mss.mss() as sct:
        shot = sct.grab(monitor)
        raw = mss.tools.to_png(shot.rgb, shot.size)
        encoded = base64.b64encode(raw).decode("ascii")
        return {
            "width": shot.width,
            "height": shot.height,
            "monitor": {
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"],
            },
            "imageBase64": encoded,
        }


def screenshot_as_base64(screen_number: int | None = None) -> dict[str, Any]:
    with mss.mss() as sct:
        monitors = sct.monitors
        if screen_number is None:
            monitor = monitors[0]
        else:
            if screen_number < 1 or screen_number >= len(monitors):
                raise HTTPException(
                    status_code=400,
                    detail=f"Screen {screen_number} is unavailable. Valid screens: 1-{len(monitors) - 1}",
                )
            monitor = monitors[screen_number]
    return capture_monitor(monitor)


def get_monitor_info(screen_number: int) -> dict[str, int]:
    with mss.mss() as sct:
        monitors = sct.monitors
        if screen_number < 1 or screen_number >= len(monitors):
            raise HTTPException(
                status_code=400,
                detail=f"Screen {screen_number} is unavailable. Valid screens: 1-{len(monitors) - 1}",
            )
        monitor = monitors[screen_number]
        return {
            "left": monitor["left"],
            "top": monitor["top"],
            "width": monitor["width"],
            "height": monitor["height"],
        }


def open_roblox_game(game_id: str) -> dict[str, Any]:
    game_id = str(game_id).strip()
    if not game_id.isdigit() or not game_id:
        raise HTTPException(status_code=400, detail="Roblox game id must contain only numbers")
    url = f"roblox://experiences/start?placeId={game_id}"
    try:
        if platform.system() == "Windows":
            os.startfile(url)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open Roblox: {exc}") from exc
    return {"ok": True, "url": url}


def resolve_path(value: str, label: str) -> str:
    requested_path = str(value or "").strip()
    if not requested_path:
        raise HTTPException(status_code=400, detail=f"A {label} is required")
    return os.path.abspath(os.path.expanduser(os.path.expandvars(requested_path)))


def is_protected_folder(path: str) -> bool:
    resolved = os.path.realpath(path)
    protected_paths = {
        os.path.realpath(os.path.abspath(os.sep)),
        os.path.realpath(os.path.expanduser("~")),
        os.path.realpath(HERE),
    }

    for name in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(name)
        if value:
            protected_paths.add(os.path.realpath(os.path.abspath(value)))

    drive, tail = os.path.splitdrive(resolved)
    if drive and tail in ("\\", "/"):
        return True

    return resolved in protected_paths


def normalize_app_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def app_match_score(query: str, candidate: str) -> tuple[int, int, str] | None:
    normalized_query = normalize_app_name(query)
    normalized_candidate = normalize_app_name(candidate)
    if not normalized_query or not normalized_candidate:
        return None
    if normalized_candidate == normalized_query:
        return 0, len(normalized_candidate), candidate.casefold()
    if normalized_candidate.startswith(normalized_query):
        return 1, len(normalized_candidate), candidate.casefold()
    if normalized_query in normalized_candidate:
        return 2, len(normalized_candidate), candidate.casefold()
    if normalized_candidate in normalized_query:
        return 3, len(normalized_candidate), candidate.casefold()
    return None


def get_windows_start_apps() -> list[dict[str, str]]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return []

    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]

    return [
        {"name": str(item.get("Name", "")), "id": str(item.get("AppID", ""))}
        for item in data
        if isinstance(item, dict) and item.get("Name") and item.get("AppID")
    ]


def find_windows_app(app_name: str) -> tuple[str, str] | None:
    best: tuple[tuple[int, int, str], str, str] | None = None

    for app_item in get_windows_start_apps():
        score = app_match_score(app_name, app_item["name"])
        if score is not None and (best is None or score < best[0]):
            best = (score, app_item["name"], app_item["id"])

    if best is not None:
        return best[1], best[2]

    executable_names = [app_name, f"{app_name}.exe"]

    for executable_name in executable_names:
        found = shutil.which(executable_name)
        if found:
            return os.path.basename(found), found

    windows_apps = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
    if windows_apps and os.path.isdir(windows_apps):
        with suppress(OSError):
            for filename in os.listdir(windows_apps):
                if filename.lower().endswith(".exe"):
                    score = app_match_score(app_name, os.path.splitext(filename)[0])
                    if score is not None and (best is None or score < best[0]):
                        best = (score, filename, os.path.join(windows_apps, filename))

    start_menu_paths = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]

    for start_menu_path in start_menu_paths:
        if not start_menu_path or not os.path.isdir(start_menu_path):
            continue
        for root, _, files in os.walk(start_menu_path):
            for filename in files:
                if not filename.lower().endswith((".lnk", ".url", ".exe")):
                    continue
                score = app_match_score(app_name, os.path.splitext(filename)[0])
                if score is not None and (best is None or score < best[0]):
                    best = (score, filename, os.path.join(root, filename))

    if best is None:
        return None

    return best[1], best[2]


def open_app_by_name(app_name: str) -> dict[str, Any]:
    app_name = str(app_name or "").strip()
    if not app_name:
        raise HTTPException(status_code=400, detail="An app name is required")
    if any(character in app_name for character in ("\\", "/", ":")):
        raise HTTPException(status_code=400, detail="Open app needs an app name, not a path")

    try:
        if platform.system() == "Windows":
            app_item = find_windows_app(app_name)
            if not app_item:
                raise HTTPException(status_code=404, detail=f"Could not find an app named: {app_name}")
            match_name, launch_target = app_item
            if launch_target.startswith("shell:AppsFolder"):
                subprocess.Popen(
                    ["explorer.exe", launch_target],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            elif launch_target.startswith("C:") or launch_target.startswith("\\"):
                os.startfile(launch_target)
            else:
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{launch_target}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return {"ok": True, "app": match_name}
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "app": app_name}

        executable = shutil.which(app_name)
        if not executable:
            raise HTTPException(status_code=404, detail=f"Could not find an app named: {app_name}")
        subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "app": app_name}
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open app: {exc}") from exc


def execute_action(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action_type == "mouse.move":
        x = int(payload.get("x", 0))
        y = int(payload.get("y", 0))
        duration = max(float(payload.get("duration", 0)), 0)
        pyautogui.moveTo(x, y, duration=duration)
        x, y = pyautogui.position()
        return {"ok": True, "x": x, "y": y}

    if action_type == "mouse.moveBy":
        dx = int(payload.get("dx", 0))
        dy = int(payload.get("dy", 0))
        duration = max(float(payload.get("duration", 0)), 0)
        pyautogui.move(dx, dy, duration=duration)
        x, y = pyautogui.position()
        return {"ok": True, "x": x, "y": y}

    if action_type == "mouse.down":
        pyautogui.mouseDown(button=normalize_button(str(payload.get("button", "left"))))
        return {"ok": True}

    if action_type == "mouse.up":
        pyautogui.mouseUp(button=normalize_button(str(payload.get("button", "left"))))
        return {"ok": True}

    if action_type == "mouse.click":
        pyautogui.click(
            button=normalize_button(str(payload.get("button", "left"))),
            clicks=max(int(payload.get("clicks", 1)), 1),
            interval=max(float(payload.get("interval", 0)), 0),
        )
        return {"ok": True}

    if action_type == "keyboard.down":
        pyautogui.keyDown(normalize_key(str(payload.get("key", ""))))
        return {"ok": True}

    if action_type == "keyboard.up":
        pyautogui.keyUp(normalize_key(str(payload.get("key", ""))))
        return {"ok": True}

    if action_type == "keyboard.press":
        pyautogui.press(normalize_key(str(payload.get("key", ""))))
        return {"ok": True}

    if action_type == "keyboard.hotkey":
        keys = [normalize_key(str(key)) for key in payload.get("keys", [])]
        if not keys:
            raise HTTPException(status_code=400, detail="At least one key is required")
        pyautogui.hotkey(*keys)
        return {"ok": True}

    if action_type == "keyboard.write":
        pyautogui.write(str(payload.get("text", "")), interval=max(float(payload.get("interval", 0)), 0))
        return {"ok": True}

    if action_type == "wait":
        time.sleep(max(float(payload.get("seconds", 0)), 0))
        return {"ok": True}

    if action_type == "roblox.openGame":
        return open_roblox_game(str(payload.get("id", "")))

    raise HTTPException(status_code=400, detail=f"Unsupported buffered action: {action_type}")


@app.get("/")
def root() -> dict[str, Any]:
    store = get_store()
    return {
        "name": "ScratchLink",
        "status": "ok",
        "mode": "cloudflare-app",
        "connectionCount": store.count(),
        "apiBaseUrl": STATE.api_base_url(),
        "docs": "/docs",
    }


@app.get("/app", response_class=HTMLResponse)
def app_shell(token: str = Query(default="")) -> HTMLResponse:
    if not token or not secrets.compare_digest(token, STATE.admin_token):
        raise HTTPException(status_code=403, detail="ScratchLink app token was rejected")

    index_path = os.path.join(UI_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as handle:
            html = handle.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not load the ScratchLink app shell: {exc}") from exc

    bootstrap = json.dumps(
        {
            "adminToken": STATE.admin_token,
            "publicApiUrl": STATE.api_base_url(),
            "docsUrl": f"{STATE.api_base_url()}/docs",
        }
    )
    html = html.replace("__SCRATCHLINK_BOOTSTRAP__", bootstrap)
    return HTMLResponse(html)


@app.get("/app/assets/{asset_name}")
def app_asset(asset_name: str) -> FileResponse:
    allowed = {"app.css", "app.js"}
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink asset")
    asset_path = os.path.join(UI_DIR, asset_name)
    if not os.path.isfile(asset_path):
        raise HTTPException(status_code=404, detail="ScratchLink asset file is missing")
    media_type = "text/css" if asset_name.endswith(".css") else "application/javascript"
    return FileResponse(asset_path, media_type=media_type)


@app.get("/admin/state")
def admin_state(_: None = Depends(require_admin)) -> dict[str, Any]:
    connections = get_store().list_connections()
    return {
        "publicApiUrl": STATE.api_base_url(),
        "docsUrl": f"{STATE.api_base_url()}/docs",
        "connectionCount": len(connections),
        "enabledCount": sum(1 for item in connections if item.enabled),
        "connections": [serialize_connection(item) for item in connections],
    }


@app.post("/admin/connections")
def admin_create_connection(payload: ConnectionCreateRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    connection = get_store().create_connection(payload.name, payload.hf_token)
    return {"connection": serialize_connection(connection)}


@app.post("/admin/connections/{connection_id}/rename")
def admin_rename_connection(connection_id: str, payload: ConnectionRenameRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    try:
        connection = get_store().rename_connection(connection_id, payload.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink connection") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connection": serialize_connection(connection)}


@app.post("/admin/connections/{connection_id}/hf-token")
def admin_set_connection_hf_token(connection_id: str, payload: ConnectionTokenRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    try:
        connection = get_store().set_hf_token(connection_id, payload.hf_token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink connection") from exc
    return {"connection": serialize_connection(connection)}


@app.post("/admin/connections/{connection_id}/toggle")
def admin_toggle_connection(connection_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    try:
        connection = get_store().toggle_connection(connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink connection") from exc
    return {"connection": serialize_connection(connection)}


@app.post("/admin/connections/{connection_id}/regenerate-password")
def admin_regenerate_connection_password(connection_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    try:
        connection = get_store().regenerate_password(connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink connection") from exc
    return {"connection": serialize_connection(connection)}


@app.delete("/admin/connections/{connection_id}")
def admin_delete_connection(connection_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    try:
        get_store().delete_connection(connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown ScratchLink connection") from exc

    if not get_store().count():
        get_store().create_connection("Main Connection")

    return {"ok": True}


@app.post("/hosted-directories/open")
def open_hosted_directory(payload: HostedDirectoryRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    directory = STATE.directory_host.open_directory(connection.id, payload.name)
    return {
        "ok": True,
        "directory": directory.name,
        "url": f"{STATE.api_base_url().rstrip('/')}/directory/{directory.name}",
    }


@app.post("/hosted-directories/close")
def close_hosted_directory(payload: HostedDirectoryRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    directory = STATE.directory_host.close_directory(connection.id, payload.name)
    return {"ok": True, "directory": directory.name}


@app.get("/hosted-directories/waiting")
def get_hosted_directory_waiting_requests(name: str, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    waiting = STATE.directory_host.list_waiting_requests(connection.id, name)
    return {
        "directory": STATE.directory_host.get_directory(connection.id, name).name,
        "count": len(waiting),
        "requests": [serialize_hosted_request(item) for item in waiting],
    }


@app.post("/hosted-directories/respond")
def respond_to_hosted_request(payload: HostedRequestResponsePayload, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    record = STATE.directory_host.respond_to_request(
        connection.id,
        payload.request_id,
        payload.status,
        parse_headers_json(payload.headers),
        str(payload.body or ""),
    )
    return {"ok": True, "requestId": record.request_id}


@app.get("/screen-state")
def get_screen_state(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    return STATE.screen_manager.serialize(connection.id)


@app.post("/screen/clear")
def clear_screen(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.clear_screen(connection.id)
    return {"ok": True}


@app.post("/screen/mode")
def set_screen_mode(payload: ScreenModeRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    state = STATE.screen_manager.set_mode(connection.id, payload.mode)
    return {"ok": True, "mode": state.mode}


@app.post("/screen/resolution")
def set_screen_resolution(payload: ScreenResolutionRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    state = STATE.screen_manager.set_resolution(connection.id, payload.width, payload.height)
    return {"ok": True, "width": state.width, "height": state.height}


@app.post("/screen/pixel")
def set_screen_pixel(payload: ScreenPixelRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.set_pixel(connection.id, payload.x, payload.y, payload.color)
    return {"ok": True}


@app.post("/screen/image")
def set_screen_image(payload: ScreenImageRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.set_image_data_uri(connection.id, payload.data_uri)
    return {"ok": True}


@app.post("/screen/analytic")
def add_screen_analytic(payload: ScreenAnalyticCreateRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.add_analytic(connection.id, payload.object_id, payload.name, payload.value, payload.kind)
    return {"ok": True}


@app.post("/screen/analytic/value")
def update_screen_analytic_value(payload: ScreenAnalyticUpdateRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.update_analytic_value(connection.id, payload.object_id, payload.value)
    return {"ok": True}


@app.post("/screen/analytic/remove")
def remove_screen_analytic(payload: ScreenItemRemoveRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.remove_analytic(connection.id, payload.object_id)
    return {"ok": True}


@app.post("/screen/object/button")
def add_screen_button(payload: ScreenButtonRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.add_button(
        connection.id,
        payload.object_id,
        payload.text,
        payload.x,
        payload.y,
        payload.width,
        payload.height,
        payload.background,
        payload.color,
    )
    return {"ok": True}


@app.post("/screen/object/text")
def add_screen_text(payload: ScreenTextRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.add_text(
        connection.id,
        payload.object_id,
        payload.text,
        payload.x,
        payload.y,
        payload.color,
        payload.font_size,
    )
    return {"ok": True}


@app.post("/screen/object/text/update")
def update_screen_text(payload: ScreenTextUpdateRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.update_text(connection.id, payload.object_id, payload.text)
    return {"ok": True}


@app.post("/screen/object/button/update")
def update_screen_button_text(payload: ScreenButtonTextUpdateRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.update_button_text(connection.id, payload.object_id, payload.text)
    return {"ok": True}


@app.post("/screen/object/box")
def add_screen_box(payload: ScreenBoxRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.add_box(
        connection.id,
        payload.object_id,
        payload.x,
        payload.y,
        payload.width,
        payload.height,
        payload.background,
    )
    return {"ok": True}


@app.post("/screen/object/remove")
def remove_screen_object(payload: ScreenItemRemoveRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.remove_object(connection.id, payload.object_id)
    return {"ok": True}


@app.post("/http/get")
def outbound_http_get(payload: OutboundHttpRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return perform_outbound_http_request("GET", payload.url, parse_headers_json(payload.headers))


@app.post("/http/post")
def outbound_http_post(payload: OutboundHttpRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return perform_outbound_http_request("POST", payload.url, parse_headers_json(payload.headers), payload.body)


@app.post("/ai/generate")
def ai_generate(payload: AiGenerateRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    return STATE.ai_manager.generate(
        connection,
        payload.prompt,
        payload.instructions,
        payload.json_format,
        payload.api_key,
        payload.backup_api_key,
    )


@app.get("/screen/buttons")
def get_screen_pressed_buttons(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    return {"buttons": STATE.screen_manager.serialize(connection.id)["pressedButtonIds"]}


@app.post("/screen/buttons/clear")
def clear_screen_pressed_buttons(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    STATE.screen_manager.clear_pressed_buttons(connection.id)
    return {"ok": True}


@app.get("/admin/screen/{connection_id}")
def admin_get_screen_state(connection_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    get_store().require(connection_id)
    return STATE.screen_manager.serialize(connection_id)


@app.post("/admin/screen/{connection_id}/press/{object_id}")
def admin_press_screen_button(connection_id: str, object_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    get_store().require(connection_id)
    STATE.screen_manager.record_button_press(connection_id, object_id)
    return {"ok": True}


@app.api_route("/directory/{directory_name}", methods=["GET", "POST"])
@app.api_route("/directory/{directory_name}/{request_path:path}", methods=["GET", "POST"])
async def public_directory_request(directory_name: str, request: Request, request_path: str = "") -> Response:
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    filtered_headers = {
        str(key): str(value)
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "x-forwarded-for", "cf-ray", "cf-connecting-ip"}
    }
    path_suffix = f"/{request_path}" if request_path else ""
    record = STATE.directory_host.queue_request(
        directory_name=directory_name,
        method=request.method,
        path=path_suffix or "/",
        query=request.url.query,
        headers=filtered_headers,
        body_text=body_text,
    )

    responded = await asyncio.to_thread(record.event.wait, 300)
    if not responded:
        STATE.directory_host.finish_request(record.request_id)
        return Response(content="ScratchLink request timed out", status_code=504, media_type="text/plain")

    headers = record.response_headers or {}
    content_type = headers.get("Content-Type") or headers.get("content-type")
    response = Response(content=record.response_body, status_code=record.status_code, media_type=content_type)
    for key, value in headers.items():
        if key.lower() == "content-type":
            continue
        response.headers[key] = value
    STATE.directory_host.finish_request(record.request_id)
    return response


@app.get("/health")
def health(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    width, height = pyautogui.size()
    x, y = pyautogui.position()
    return {
        "ok": True,
        "connection": {"id": connection.id, "name": connection.name},
        "screen": {"width": width, "height": height},
        "mouse": {"x": x, "y": y},
    }


@app.get("/extension/{connection_id}.js")
def extension_js(connection_id: str, password: str = Query(default="")) -> PlainTextResponse:
    connection = get_store().require(connection_id)
    if not password or not secrets.compare_digest(connection.password, password):
        raise HTTPException(status_code=403, detail="The extension password is invalid")
    return PlainTextResponse(build_extension_script(connection), media_type="application/javascript")


@app.get("/screen")
def get_screen(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return screenshot_as_base64()


@app.get("/screen/all")
def get_all_screens(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return screenshot_as_base64()


@app.get("/screen/{screen_number}")
def get_screen_number(screen_number: int, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return screenshot_as_base64(screen_number)


@app.get("/screen/info")
def get_screen_info(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    width, height = pyautogui.size()
    return {"width": width, "height": height}


@app.get("/screen/info/{screen_number}")
def get_screen_number_info(screen_number: int, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return get_monitor_info(screen_number)


@app.get("/mouse")
def get_mouse(connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    x, y = pyautogui.position()
    return {"x": x, "y": y}


@app.post("/mouse/move")
def move_mouse(payload: MouseMoveRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    pyautogui.moveTo(payload.x, payload.y, duration=max(payload.duration, 0))
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


@app.post("/mouse/move-by")
def move_mouse_by(payload: MouseOffsetRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    pyautogui.move(payload.dx, payload.dy, duration=max(payload.duration, 0))
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


@app.post("/mouse/down")
def mouse_down(payload: MouseButtonRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    pyautogui.mouseDown(button=normalize_button(payload.button))
    return {"ok": True}


@app.post("/mouse/up")
def mouse_up(payload: MouseButtonRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    pyautogui.mouseUp(button=normalize_button(payload.button))
    return {"ok": True}


@app.post("/mouse/click")
def mouse_click(payload: MouseClickRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("mouse.click", {"button": payload.button, "clicks": payload.clicks, "interval": payload.interval})


@app.post("/keyboard/down")
def key_down(payload: KeyRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("keyboard.down", {"key": payload.key})


@app.post("/keyboard/up")
def key_up(payload: KeyRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("keyboard.up", {"key": payload.key})


@app.post("/keyboard/press")
def key_press(payload: KeyRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("keyboard.press", {"key": payload.key})


@app.post("/keyboard/hotkey")
def hotkey(payload: HotkeyRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("keyboard.hotkey", {"keys": payload.keys})


@app.post("/keyboard/write")
def write_text(payload: WriteRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("keyboard.write", {"text": payload.text, "interval": payload.interval})


@app.post("/wait")
def wait_seconds(payload: WaitRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return execute_action("wait", {"seconds": payload.seconds})


@app.post("/roblox/open-game")
def roblox_open_game(payload: RobloxGameRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return open_roblox_game(payload.id)


@app.post("/file/open")
def open_file(payload: OpenFileRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    path = resolve_path(payload.path, "file path")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File or folder was not found: {path}")
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open file: {exc}") from exc
    return {"ok": True, "path": path}


@app.get("/files/list")
def get_files_under_folder(path: str, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    folder = resolve_path(path, "folder path")
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404, detail=f"Folder was not found: {folder}")

    files: list[str] = []
    for root_dir, _, filenames in os.walk(folder, followlinks=False):
        for filename in filenames:
            files.append(os.path.join(root_dir, filename))
            if len(files) >= 10000:
                return {"files": sorted(files, key=str.casefold), "truncated": True}

    return {"files": sorted(files, key=str.casefold), "truncated": False}


@app.get("/files/read")
def read_file(path: str, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    file_path = resolve_path(path, "file path")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"File was not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read file: {exc}") from exc

    return {"text": text}


@app.post("/files/write")
def write_file(payload: FileWriteRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    file_path = resolve_path(payload.path, "file path")
    parent = os.path.dirname(file_path)

    try:
        os.makedirs(parent, exist_ok=True)
        with open(file_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload.text)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write file: {exc}") from exc

    return {"ok": True, "path": file_path}


@app.post("/folders/create")
def create_folder(payload: FolderRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    folder = resolve_path(payload.path, "folder path")

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}") from exc

    return {"ok": True, "path": folder}


@app.post("/folders/destroy")
def destroy_folder(payload: FolderRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    folder = resolve_path(payload.path, "folder path")
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404, detail=f"Folder was not found: {folder}")
    if is_protected_folder(folder):
        raise HTTPException(status_code=400, detail="That folder cannot be destroyed")

    try:
        shutil.rmtree(folder)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not destroy folder: {exc}") from exc

    return {"ok": True, "path": folder}


@app.post("/app/open")
def open_app(payload: OpenAppRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    return open_app_by_name(payload.name)


@app.post("/batch")
def run_batch(payload: BatchRequest, connection: ConnectionRecord = Depends(require_connection)) -> dict[str, Any]:
    _ = connection
    results = [execute_action(action.type, action.payload) for action in payload.actions]
    return {"ok": True, "count": len(results), "results": results}


def find_cloudflared_executable() -> str | None:
    cloudflared = shutil.which("cloudflared")
    if cloudflared:
        return cloudflared

    if os.path.isfile(CLOUDFLARED_FALLBACK_PATH):
        return CLOUDFLARED_FALLBACK_PATH

    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")

    candidates = [
        os.path.join(local_appdata, "Microsoft", "WinGet", "Packages"),
        os.path.join(program_files, "cloudflared"),
        os.path.join(program_files_x86, "cloudflared"),
        os.path.join(program_files, "Cloudflare", "Cloudflared"),
        os.path.join(program_files_x86, "Cloudflare", "Cloudflared"),
    ]

    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        direct = os.path.join(base, "cloudflared.exe")
        if os.path.isfile(direct):
            return direct
        for root_dir, _, files in os.walk(base):
            if "cloudflared.exe" in files:
                return os.path.join(root_dir, "cloudflared.exe")

    return None


def install_cloudflared_with_winget(progress: Callable[[str], None] | None = None) -> str | None:
    winget = shutil.which("winget")
    if not winget:
        return None

    if progress:
        progress("Downloading Cloudflare Tunnel with winget...")

    command = [
        winget,
        "install",
        "--id",
        "Cloudflare.cloudflared",
        "--exact",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]

    try:
        result = subprocess.run(command, check=False, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None

    if result.returncode == 0:
        return find_cloudflared_executable()
    return None


def download_cloudflared_direct(progress: Callable[[str], None] | None = None) -> str | None:
    if platform.system() != "Windows":
        return None

    os.makedirs(TOOLS_DIR, exist_ok=True)
    if progress:
        progress("Downloading Cloudflare Tunnel directly...")

    try:
        with urllib.request.urlopen(CLOUDFLARED_DOWNLOAD_URL, timeout=120) as response:
            data = response.read()
        with open(CLOUDFLARED_FALLBACK_PATH, "wb") as handle:
            handle.write(data)
    except OSError:
        return None

    return CLOUDFLARED_FALLBACK_PATH if os.path.isfile(CLOUDFLARED_FALLBACK_PATH) else None


def ensure_cloudflared(progress: Callable[[str], None] | None = None) -> str:
    existing = find_cloudflared_executable()
    if existing:
        if progress:
            progress("Cloudflare Tunnel is already installed.")
        return existing

    installed = install_cloudflared_with_winget(progress)
    if installed:
        if progress:
            progress("Cloudflare Tunnel installed successfully.")
        return installed

    downloaded = download_cloudflared_direct(progress)
    if downloaded:
        if progress:
            progress("Cloudflare Tunnel downloaded successfully.")
        return downloaded

    raise RuntimeError("ScratchLink could not install Cloudflare Tunnel automatically.")


def wait_for_public_hostname(url: str, timeout_seconds: float = 20.0) -> bool:
    hostname = urlparse(str(url or "").strip()).hostname
    if not hostname:
        return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            socket.getaddrinfo(hostname, 443)
            return True
        except OSError:
            time.sleep(0.5)
    return False


class LocalServerController:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def local_base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._thread is not None:
            return
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def wait_until_started(self, timeout_seconds: float = 20.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.is_running():
                return
            time.sleep(0.2)
        raise RuntimeError("ScratchLink server did not start in time.")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)

    def is_running(self) -> bool:
        return bool(self._server and getattr(self._server, "started", False))


class TunnelController:
    def __init__(self, cloudflared_path: str, local_url: str):
        self.cloudflared_path = cloudflared_path
        self.local_url = local_url
        self._process: subprocess.Popen[str] | None = None
        self._public_url: str | None = None

    @property
    def public_url(self) -> str:
        if not self._public_url:
            raise RuntimeError("Cloudflare Tunnel is not ready yet.")
        return self._public_url

    def start(self, progress: Callable[[str], None] | None = None) -> str:
        for attempt in range(1, 4):
            if progress:
                progress(f"Opening a public Cloudflare URL for ScratchLink... (attempt {attempt}/3)")

            command = [self.cloudflared_path, "tunnel", "--url", self.local_url, "--no-autoupdate"]
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            url_holder = {"url": None}

            def collect() -> None:
                if not self._process or not self._process.stdout:
                    return
                for line in self._process.stdout:
                    text = line.strip()
                    if progress and text:
                        lowered = text.lower()
                        if "trycloudflare.com" in lowered or "registered" in lowered or "starting" in lowered:
                            progress(text)
                    if "https://" in text and "trycloudflare.com" in text and not url_holder["url"]:
                        for token in text.split():
                            if token.startswith("https://") and "trycloudflare.com" in token:
                                url_holder["url"] = token.rstrip("/")
                                self._public_url = url_holder["url"]
                                return

            thread = threading.Thread(target=collect, daemon=True)
            thread.start()

            deadline = time.time() + 60
            while time.time() < deadline:
                if url_holder["url"]:
                    if wait_for_public_hostname(url_holder["url"]):
                        return url_holder["url"]
                    if progress:
                        progress("Cloudflare gave ScratchLink a URL that is not reachable yet. Retrying...")
                    break
                if self._process and self._process.poll() is not None:
                    break
                time.sleep(0.3)

            self.stop()
            self._process = None
            self._public_url = None

        raise RuntimeError("ScratchLink could not open a working Cloudflare URL.")

    def stop(self) -> None:
        if self._process is None:
            return
        with suppress(OSError):
            self._process.terminate()
        with suppress(Exception):
            self._process.wait(timeout=3)


class StartupGate:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.server = LocalServerController(host, port)
        self.tunnel: TunnelController | None = None
        self.root = tk.Tk()
        self.root.title("ScratchLink")
        self.root.geometry("520x250")
        self.root.resizable(False, False)
        self.root.configure(bg="#eef3f8")
        self.status_var = tk.StringVar(value="Preparing ScratchLink...")
        self.detail_var = tk.StringVar(value="The app is setting up Cloudflare Tunnel before anything can be used.")
        self.error: Exception | None = None
        self.ready = threading.Event()

        shell = tk.Frame(self.root, bg="#eef3f8", padx=28, pady=28)
        shell.pack(fill="both", expand=True)
        card = tk.Frame(shell, bg="#ffffff", highlightthickness=1, highlightbackground="#d8e3ee", padx=22, pady=22)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="ScratchLink", bg="#ffffff", fg="#16324f", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(
            card,
            text="Cloudflare Tunnel is required before the app unlocks.",
            bg="#ffffff",
            fg="#4e647a",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(6, 18))
        ttk.Progressbar(card, mode="indeterminate").pack(fill="x")
        self.progressbar = card.winfo_children()[-1]
        self.progressbar.start(10)
        tk.Label(card, textvariable=self.status_var, bg="#ffffff", fg="#16324f", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(18, 4))
        tk.Label(card, textvariable=self.detail_var, bg="#ffffff", fg="#5f7388", justify="left", wraplength=420, font=("Segoe UI", 10)).pack(anchor="w")

    def set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def set_detail(self, message: str) -> None:
        self.root.after(0, lambda: self.detail_var.set(message))

    def run(self) -> tuple[LocalServerController, TunnelController]:
        worker = threading.Thread(target=self._bootstrap, daemon=True)
        worker.start()
        self.root.after(200, self._poll)
        self.root.mainloop()
        if self.error is not None:
            raise self.error
        if self.tunnel is None:
            raise RuntimeError("ScratchLink did not finish startup.")
        return self.server, self.tunnel

    def _poll(self) -> None:
        if self.ready.is_set():
            self.progressbar.stop()
            self.root.destroy()
            return
        self.root.after(200, self._poll)

    def _bootstrap(self) -> None:
        try:
            self.set_status("Checking Cloudflare Tunnel...")
            cloudflared_path = ensure_cloudflared(self.set_detail)
            self.set_status("Starting the local ScratchLink server...")
            self.server.start()
            self.server.wait_until_started()
            self.set_status("Opening a public ScratchLink URL...")
            self.tunnel = TunnelController(cloudflared_path, self.server.local_base_url)
            public_url = self.tunnel.start(self.set_detail)
            STATE.local_base_url = self.server.local_base_url
            STATE.public_base_url = public_url
            self.set_status("ScratchLink is ready.")
            self.set_detail("Cloudflare Tunnel is online and the app is unlocking now.")
            self.ready.set()
        except Exception as exc:
            self.error = exc
            self.set_status("ScratchLink could not finish startup.")
            self.set_detail(str(exc))
            self.ready.set()


class ScratchLinkApp(ttk.Frame):
    def __init__(self, root: tk.Tk, store: ConnectionStore, server: LocalServerController, tunnel: TunnelController):
        super().__init__(root, padding=18)
        self.root = root
        self.store = store
        self.server = server
        self.tunnel = tunnel
        self.selected_connection_id: str | None = None
        self.connection_cards: dict[str, tk.Frame] = {}

        self.status_var = tk.StringVar(value="Cloudflare Tunnel is live")
        self.api_url_var = tk.StringVar(value=STATE.api_base_url())
        self.docs_url_var = tk.StringVar(value=f"{STATE.api_base_url()}/docs")
        self.connection_count_var = tk.StringVar(value="0")
        self.active_count_var = tk.StringVar(value="0")
        self.detail_name_var = tk.StringVar(value="")
        self.detail_status_var = tk.StringVar(value="")
        self.detail_id_var = tk.StringVar(value="")
        self.detail_password_var = tk.StringVar(value="")
        self.detail_last_used_var = tk.StringVar(value="Never")
        self.detail_extension_url_var = tk.StringVar(value="")

        self._configure_style()
        self._build_menu()
        self._build_layout()

        if not self.store.count():
            created = self.store.create_connection("Main Connection")
            self.selected_connection_id = created.id

        self.refresh_connections()
        self.root.after(1400, self._refresh_loop)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg="#f0f4f7")
        style.configure("App.TFrame", background="#f0f4f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#f0f4f7", foreground="#17324d", font=("Segoe UI", 24, "bold"))
        style.configure("Subtle.TLabel", background="#f0f4f7", foreground="#54697d", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#17324d", font=("Segoe UI", 12, "bold"))
        style.configure("CardBody.TLabel", background="#ffffff", foreground="#3f5972", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background="#f0f4f7")
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 10, "bold"))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New Connection", command=self.create_connection)
        file_menu.add_separator()
        file_menu.add_command(label="Save Connections", command=self.store.save)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Copy Public API URL", command=lambda: self.copy_text(STATE.api_base_url(), "Public API URL copied."))
        help_menu.add_command(label="Copy Public Docs URL", command=lambda: self.copy_text(f'{STATE.api_base_url()}/docs', "Public docs URL copied."))
        help_menu.add_separator()
        help_menu.add_command(label="About ScratchLink", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_layout(self) -> None:
        self.pack(fill="both", expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="ScratchLink", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Public Cloudflare URL, named connection cards, and per-request password verification.",
            style="Subtle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, style="Subtle.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        summary = ttk.Frame(self, style="App.TFrame")
        summary.grid(row=1, column=0, sticky="ew", pady=(18, 14))
        for column in range(3):
            summary.columnconfigure(column, weight=1)

        self._build_summary_card(summary, 0, "Connections", self.connection_count_var, "Saved connection cards in the app.")
        self._build_summary_card(summary, 1, "Enabled", self.active_count_var, "Connections currently allowed to send actions.")
        self._build_summary_card(summary, 2, "Public URL", self.api_url_var, "This Cloudflare URL is the one used by the extension and API.")

        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew")

        connections_tab = ttk.Frame(notebook, padding=16)
        server_tab = ttk.Frame(notebook, padding=16)
        notebook.add(connections_tab, text="Connections")
        notebook.add(server_tab, text="Server")

        self._build_connections_tab(connections_tab)
        self._build_server_tab(server_tab)

    def _build_summary_card(self, parent: ttk.Frame, column: int, title: str, value_var: tk.StringVar, description: str) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        parent.columnconfigure(column, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, textvariable=value_var, style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(card, text=description, style="CardBody.TLabel", wraplength=260, justify="left").grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_connections_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=5)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(parent, style="App.TFrame")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Button(toolbar, text="New Connection", style="Accent.TButton", command=self.create_connection).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="Copy Extension Link", command=self.copy_extension_link).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="Turn On or Off", command=self.toggle_selected).grid(row=0, column=2)

        card_shell = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        card_shell.columnconfigure(0, weight=1)
        card_shell.rowconfigure(0, weight=1)

        self.card_canvas = tk.Canvas(card_shell, bg="#ffffff", highlightthickness=0)
        self.card_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(card_shell, orient="vertical", command=self.card_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.card_canvas.configure(yscrollcommand=scrollbar.set)

        self.cards_frame = tk.Frame(self.card_canvas, bg="#ffffff")
        self.cards_window = self.card_canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", lambda _event: self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all")))
        self.card_canvas.bind("<Configure>", self._on_cards_canvas_resize)

        details = ttk.Frame(parent, style="Card.TFrame", padding=18)
        details.grid(row=1, column=1, sticky="nsew")
        details.columnconfigure(0, weight=1)

        ttk.Label(details, text="Selected Connection", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            details,
            text="Cards glow green when enabled and red when disabled. Use the pencil menu on a card to edit it quickly.",
            style="CardBody.TLabel",
            wraplength=340,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 16))

        detail_rows = [
            ("Name", self.detail_name_var),
            ("Status", self.detail_status_var),
            ("Connection ID", self.detail_id_var),
            ("Password", self.detail_password_var),
            ("Last Used", self.detail_last_used_var),
            ("Extension Link", self.detail_extension_url_var),
        ]

        for index, (label, variable) in enumerate(detail_rows, start=2):
            ttk.Label(details, text=label, style="CardTitle.TLabel").grid(row=index * 2, column=0, sticky="w")
            entry = ttk.Entry(details, textvariable=variable)
            entry.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(4, 10))
            entry.state(["readonly"])

        button_row = ttk.Frame(details, style="Card.TFrame")
        button_row.grid(row=18, column=0, sticky="ew", pady=(8, 0))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)
        ttk.Button(button_row, text="Copy Password", command=self.copy_password).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(button_row, text="Regenerate Password", command=self.regenerate_selected).grid(row=0, column=1, sticky="ew")
        ttk.Button(button_row, text="Rename", command=self.rename_selected).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(button_row, text="Delete", command=self.delete_selected).grid(row=1, column=1, sticky="ew", pady=(8, 0))

    def _build_server_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        card = ttk.Frame(parent, style="Card.TFrame", padding=18)
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text="Server", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            card,
            text="ScratchLink now serves everything through a public Cloudflare URL. The extension and API both use that address instead of localhost.",
            style="CardBody.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 18))

        rows = [
            ("Public API URL", self.api_url_var),
            ("Public Docs URL", self.docs_url_var),
            ("Status", self.status_var),
        ]

        for index, (label, variable) in enumerate(rows, start=2):
            ttk.Label(card, text=label, style="CardTitle.TLabel").grid(row=index * 2, column=0, sticky="w")
            entry = ttk.Entry(card, textvariable=variable)
            entry.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(4, 12))
            entry.state(["readonly"])

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=10, column=0, sticky="w", pady=(4, 0))
        ttk.Button(actions, text="Copy Public API URL", command=lambda: self.copy_text(STATE.api_base_url(), "Public API URL copied.")).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Copy Public Docs URL", command=lambda: self.copy_text(f"{STATE.api_base_url()}/docs", "Public docs URL copied.")).grid(row=0, column=1)

    def _on_cards_canvas_resize(self, event: tk.Event) -> None:
        self.card_canvas.itemconfigure(self.cards_window, width=event.width)
        self.refresh_connection_cards()

    def _refresh_loop(self) -> None:
        self.refresh_connections()
        self.root.after(1400, self._refresh_loop)

    def refresh_connections(self) -> None:
        connections = self.store.list_connections()
        active_count = sum(1 for item in connections if item.enabled)
        self.connection_count_var.set(str(len(connections)))
        self.active_count_var.set(str(active_count))
        self.api_url_var.set(STATE.api_base_url())
        self.docs_url_var.set(f"{STATE.api_base_url()}/docs")
        self.status_var.set("Cloudflare Tunnel is live" if self.server.is_running() else "Waiting for ScratchLink server")

        if self.selected_connection_id and any(item.id == self.selected_connection_id for item in connections):
            pass
        elif connections:
            self.selected_connection_id = connections[0].id
        else:
            self.selected_connection_id = None

        self.refresh_connection_cards(connections)
        self.refresh_details()

    def refresh_connection_cards(self, connections: list[ConnectionRecord] | None = None) -> None:
        if connections is None:
            connections = self.store.list_connections()

        for widget in self.cards_frame.winfo_children():
            widget.destroy()
        self.connection_cards.clear()

        if not connections:
            empty = tk.Label(self.cards_frame, text="No connections yet.", bg="#ffffff", fg="#54697d", font=("Segoe UI", 11))
            empty.grid(row=0, column=0, padx=10, pady=10, sticky="w")
            return

        width = max(self.card_canvas.winfo_width(), 640)
        columns = 3 if width > 980 else 2 if width > 620 else 1

        for column in range(columns):
            self.cards_frame.grid_columnconfigure(column, weight=1)

        for index, connection in enumerate(connections):
            row = index // columns
            column = index % columns
            card = self._create_connection_card(self.cards_frame, connection)
            card.grid(row=row, column=column, padx=10, pady=10, sticky="nsew")
            self.connection_cards[connection.id] = card

    def _create_connection_card(self, parent: tk.Frame, connection: ConnectionRecord) -> tk.Frame:
        enabled = connection.enabled
        accent = "#3dbb70" if enabled else "#d95b63"
        accent_soft = "#ecfbf1" if enabled else "#fff0f1"
        border = "#f0c24c" if connection.id == self.selected_connection_id else accent

        outer = tk.Frame(parent, bg=border, highlightthickness=0, bd=0)
        card = tk.Frame(outer, bg="#ffffff", padx=14, pady=14)
        card.pack(fill="both", expand=True, padx=2, pady=2)
        top = tk.Frame(card, bg="#ffffff")
        top.pack(fill="x")

        status_badge = tk.Label(
            top,
            text="Enabled" if enabled else "Disabled",
            bg=accent_soft,
            fg=accent,
            padx=10,
            pady=4,
            font=("Segoe UI", 9, "bold"),
        )
        status_badge.pack(side="left")

        edit_button = tk.Button(
            top,
            text="✎",
            bg="#ffffff",
            fg="#40586f",
            activebackground="#ffffff",
            activeforeground="#17324d",
            bd=0,
            font=("Segoe UI Symbol", 13),
            cursor="hand2",
            command=lambda cid=connection.id: self.open_connection_menu(cid),
        )
        edit_button.pack(side="right")

        tk.Label(card, text=connection.name, bg="#ffffff", fg="#17324d", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(12, 4))
        tk.Label(card, text=shorten_connection_id(connection.id), bg="#ffffff", fg="#607489", font=("Consolas", 10)).pack(anchor="w")
        tk.Label(card, text=f"Last used: {format_timestamp(connection.last_used_at)}", bg="#ffffff", fg="#50657b", font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 0))

        footer = tk.Frame(card, bg="#ffffff")
        footer.pack(fill="x", pady=(14, 0))
        tk.Label(footer, text="Public link ready", bg="#ffffff", fg=accent, font=("Segoe UI", 9, "bold")).pack(side="left")
        quick = tk.Button(
            footer,
            text="Select",
            bg=accent,
            fg="#ffffff",
            activebackground=accent,
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            command=lambda cid=connection.id: self.select_connection(cid),
        )
        quick.pack(side="right")

        for widget in (outer, card, top, status_badge):
            widget.bind("<Button-1>", lambda _event, cid=connection.id: self.select_connection(cid))

        return outer

    def select_connection(self, connection_id: str) -> None:
        self.selected_connection_id = connection_id
        self.refresh_connections()

    def open_connection_menu(self, connection_id: str) -> None:
        self.selected_connection_id = connection_id
        self.refresh_details()
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Rename", command=self.rename_selected)
        menu.add_command(label="Turn On or Off", command=self.toggle_selected)
        menu.add_command(label="Copy Extension Link", command=self.copy_extension_link)
        menu.add_command(label="Copy Password", command=self.copy_password)
        menu.add_command(label="Regenerate Password", command=self.regenerate_selected)
        menu.add_separator()
        menu.add_command(label="Delete", command=self.delete_selected)
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        menu.tk_popup(x, y)
        menu.grab_release()

    def refresh_details(self) -> None:
        if not self.selected_connection_id:
            for variable in (
                self.detail_name_var,
                self.detail_status_var,
                self.detail_id_var,
                self.detail_password_var,
                self.detail_last_used_var,
                self.detail_extension_url_var,
            ):
                variable.set("")
            return

        connection = self.store.get(self.selected_connection_id)
        if connection is None:
            self.selected_connection_id = None
            self.refresh_details()
            return

        self.detail_name_var.set(connection.name)
        self.detail_status_var.set("Enabled" if connection.enabled else "Disabled")
        self.detail_id_var.set(connection.id)
        self.detail_password_var.set(connection.password)
        self.detail_last_used_var.set(format_timestamp(connection.last_used_at))
        self.detail_extension_url_var.set(self.make_extension_url(connection))

    def make_extension_url(self, connection: ConnectionRecord) -> str:
        return f"{STATE.api_base_url().rstrip('/')}/extension/{connection.id}.js?password={quote(connection.password)}"

    def copy_text(self, text: str, message: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(message)

    def get_selected_connection(self) -> ConnectionRecord | None:
        if not self.selected_connection_id:
            messagebox.showinfo("ScratchLink", "Choose a connection first.")
            return None
        connection = self.store.get(self.selected_connection_id)
        if connection is None:
            messagebox.showinfo("ScratchLink", "That connection is no longer available.")
            self.refresh_connections()
            return None
        return connection

    def create_connection(self) -> None:
        name = simpledialog.askstring("New Connection", "Choose a name for the new connection:", parent=self.root)
        connection = self.store.create_connection(name)
        self.selected_connection_id = connection.id
        self.refresh_connections()
        self.copy_text(self.make_extension_url(connection), "New connection created and extension link copied.")

    def rename_selected(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        new_name = simpledialog.askstring("Rename Connection", "Choose a new name:", initialvalue=connection.name, parent=self.root)
        if new_name is None:
            return
        try:
            updated = self.store.rename_connection(connection.id, new_name)
        except ValueError as exc:
            messagebox.showerror("ScratchLink", str(exc))
            return
        self.selected_connection_id = updated.id
        self.refresh_connections()

    def toggle_selected(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        updated = self.store.toggle_connection(connection.id)
        self.selected_connection_id = updated.id
        self.refresh_connections()

    def copy_extension_link(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        self.copy_text(self.make_extension_url(connection), "Extension link copied.")

    def copy_password(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        self.copy_text(connection.password, "Password copied.")

    def regenerate_selected(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        confirmed = messagebox.askyesno(
            "Regenerate Password",
            "This will disconnect any project still using the old password. Continue?",
            parent=self.root,
        )
        if not confirmed:
            return
        updated = self.store.regenerate_password(connection.id)
        self.selected_connection_id = updated.id
        self.refresh_connections()
        self.copy_text(self.make_extension_url(updated), "New extension link copied after password reset.")

    def delete_selected(self) -> None:
        connection = self.get_selected_connection()
        if connection is None:
            return
        confirmed = messagebox.askyesno(
            "Delete Connection",
            f"Delete '{connection.name}'? Projects using it will stop working until you add a new connection.",
            parent=self.root,
        )
        if not confirmed:
            return
        self.store.delete_connection(connection.id)
        if not self.store.count():
            created = self.store.create_connection("Main Connection")
            self.selected_connection_id = created.id
        else:
            self.selected_connection_id = None
        self.refresh_connections()

    def show_about(self) -> None:
        messagebox.showinfo(
            "About ScratchLink",
            "ScratchLink is a local desktop bridge for PenguinMod projects, now routed through a public Cloudflare URL.",
            parent=self.root,
        )

    def on_close(self) -> None:
        self.root.destroy()


def build_root_window() -> tk.Tk:
    root = tk.Tk()
    root.title("ScratchLink")
    root.geometry("1220x790")
    root.minsize(1060, 720)
    return root


LOADING_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ScratchLink</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef3f7;
      --panel: rgba(255,255,255,0.92);
      --ink: #17324d;
      --soft: #5a7188;
      --line: #d8e2ec;
      --accent: #1f8f68;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(31,143,104,0.18), transparent 28%),
        radial-gradient(circle at bottom right, rgba(25,92,146,0.14), transparent 26%),
        var(--bg);
      color: var(--ink);
      display: grid;
      place-items: center;
    }
    .panel {
      width: min(560px, calc(100vw - 48px));
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 28px 72px rgba(23, 50, 77, 0.14);
      backdrop-filter: blur(10px);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 30px;
    }
    p {
      margin: 0;
      color: var(--soft);
      line-height: 1.5;
    }
    .bar {
      margin: 22px 0 18px;
      width: 100%;
      height: 12px;
      border-radius: 999px;
      background: #e5edf5;
      overflow: hidden;
    }
    .bar span {
      display: block;
      width: 48%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #1f8f68, #46c697);
      transform: translateX(-100%);
      animation: pulse 1.5s ease-in-out infinite;
    }
    .status {
      margin-top: 14px;
      font-size: 15px;
      font-weight: 700;
    }
    .detail {
      margin-top: 8px;
      font-size: 14px;
      color: var(--soft);
    }
    @keyframes pulse {
      0% { transform: translateX(0%); }
      50% { transform: translateX(108%); }
      100% { transform: translateX(0%); }
    }
  </style>
</head>
<body>
  <section class="panel">
    <h1>ScratchLink</h1>
    <p>The app is preparing Cloudflare and bringing the dashboard online.</p>
    <div class="bar"><span></span></div>
    <div class="status" id="status">Preparing ScratchLink...</div>
    <div class="detail" id="detail">Please wait while the public connection is created.</div>
  </section>
  <script>
    window.setScratchLinkStatus = function(status, detail) {
      document.getElementById('status').textContent = status || '';
      document.getElementById('detail').textContent = detail || '';
    };
  </script>
</body>
</html>
"""


def update_loading_window(window: webview.Window, status: str, detail: str) -> None:
    escaped_status = json.dumps(status)
    escaped_detail = json.dumps(detail)
    with suppress(Exception):
        window.evaluate_js(f"window.setScratchLinkStatus({escaped_status}, {escaped_detail});")


def bootstrap_webview(window: webview.Window, host: str, port: int) -> tuple[LocalServerController, TunnelController]:
    update_loading_window(window, "Checking Cloudflare Tunnel...", "ScratchLink is making sure the tunnel tool is ready.")
    cloudflared_path = ensure_cloudflared(lambda message: update_loading_window(window, "Preparing Cloudflare Tunnel...", message))

    STATE.store = ConnectionStore()
    if not STATE.store.count():
        STATE.store.create_connection("Main Connection")

    update_loading_window(window, "Starting the local ScratchLink server...", "The background API is coming online.")
    server = LocalServerController(host, port)
    server.start()
    server.wait_until_started()

    update_loading_window(window, "Opening a public ScratchLink URL...", "Cloudflare is publishing the app URL now.")
    tunnel = TunnelController(cloudflared_path, server.local_base_url)
    public_url = tunnel.start(lambda message: update_loading_window(window, "Opening a public ScratchLink URL...", message))

    STATE.local_base_url = server.local_base_url
    STATE.public_base_url = public_url

    update_loading_window(window, "Opening the ScratchLink dashboard...", "The HTML interface is loading.")
    window.load_url(f"{server.local_base_url}/app?token={quote(STATE.admin_token)}")
    return server, tunnel


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the ScratchLink desktop app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    STATE.extension_template = load_extension_template()
    STATE.local_base_url = f"http://{args.host}:{args.port}"
    STATE.public_base_url = ""
    server: LocalServerController | None = None
    tunnel: TunnelController | None = None
    startup_error: Exception | None = None

    def startup(_window: webview.Window) -> None:
        nonlocal server, tunnel, startup_error
        try:
            server, tunnel = bootstrap_webview(window, args.host, args.port)
        except Exception as exc:
            startup_error = exc
            update_loading_window(window, "ScratchLink could not start.", str(exc))

    window = webview.create_window(
        "ScratchLink",
        html=LOADING_HTML,
        width=1260,
        height=820,
        min_size=(1080, 720),
        background_color="#eef3f7",
    )

    try:
        webview.start(startup, window)
    finally:
        if tunnel is not None:
            tunnel.stop()
        if server is not None:
            server.stop()

    if startup_error is not None:
        raise startup_error


if __name__ == "__main__":
    main()
