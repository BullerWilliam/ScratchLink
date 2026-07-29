import argparse
import base64
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from typing import Any

import mss
import mss.tools
import pyautogui
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENSION_FILE = os.path.join(HERE, "flowmacro_penguinmod.js")
SESSION_ID = uuid.uuid4().hex
EXTENSION_TEMPLATE = ""

MOUSE_BUTTONS = {"left", "middle", "right"}
KEY_ALIASES = {
    "windows": "win",
    "meta": "win",
    "super": "win",
    "command": "command",
    "cmd": "command",
}


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


app = FastAPI(title="FlowMacro Scratch Link")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_extension_template() -> str:
    with open(EXTENSION_FILE, "r", encoding="utf-8") as handle:
        return handle.read()


def build_extension_script(base_url: str) -> str:
    return (
        EXTENSION_TEMPLATE
        .replace("__FLOWMACRO_SESSION_ID__", SESSION_ID)
        .replace("__FLOWMACRO_BASE_URL__", base_url.rstrip("/"))
    )


def require_session(x_flowmacro_session: str | None = Header(default=None)) -> None:
    if x_flowmacro_session != SESSION_ID:
        raise HTTPException(status_code=403, detail="Invalid FlowMacro session")


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

    for app in get_windows_start_apps():
        score = app_match_score(app_name, app["name"])
        if score is not None and (best is None or score < best[0]):
            best = (score, app["name"], app["id"])

    if best is not None:
        return best[1], best[2]

    target_name = normalize_app_name(app_name)
    executable_names = [app_name, f"{app_name}.exe"]

    for executable_name in executable_names:
        found = shutil.which(executable_name)
        if found:
            return os.path.basename(found), found

    windows_apps = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
    if windows_apps and os.path.isdir(windows_apps):
        try:
            for filename in os.listdir(windows_apps):
                if filename.lower().endswith(".exe"):
                    score = app_match_score(app_name, os.path.splitext(filename)[0])
                    if score is not None and (best is None or score < best[0]):
                        best = (score, filename, os.path.join(windows_apps, filename))
        except OSError:
            pass

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
            app = find_windows_app(app_name)
            if not app:
                raise HTTPException(status_code=404, detail=f"Could not find an app named: {app_name}")
            match_name, launch_target = app
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
    return {
        "name": "FlowMacro Scratch Link",
        "status": "ok",
        "sessionId": SESSION_ID,
        "extensionUrl": f"/extension/{SESSION_ID}.js",
        "docs": "/docs",
    }


@app.get("/health")
def health(x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    width, height = pyautogui.size()
    x, y = pyautogui.position()
    return {
        "ok": True,
        "screen": {"width": width, "height": height},
        "mouse": {"x": x, "y": y},
    }


@app.get("/extension/{session_id}.js")
def extension_js(session_id: str, request: Request) -> PlainTextResponse:
    if session_id != SESSION_ID:
        raise HTTPException(status_code=404, detail="Unknown extension session")
    base_url = str(request.base_url).rstrip("/")
    script = build_extension_script(base_url)
    return PlainTextResponse(script, media_type="application/javascript")


@app.get("/screen")
def get_screen(x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return screenshot_as_base64()


@app.get("/screen/all")
def get_all_screens(x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return screenshot_as_base64()


@app.get("/screen/{screen_number}")
def get_screen_number(screen_number: int, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return screenshot_as_base64(screen_number)


@app.get("/screen/info")
def get_screen_info(x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    width, height = pyautogui.size()
    return {"width": width, "height": height}


@app.get("/screen/info/{screen_number}")
def get_screen_number_info(screen_number: int, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return get_monitor_info(screen_number)


@app.get("/mouse")
def get_mouse(x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    x, y = pyautogui.position()
    return {"x": x, "y": y}


@app.post("/mouse/move")
def move_mouse(payload: MouseMoveRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    pyautogui.moveTo(payload.x, payload.y, duration=max(payload.duration, 0))
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


@app.post("/mouse/move-by")
def move_mouse_by(payload: MouseOffsetRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    pyautogui.move(payload.dx, payload.dy, duration=max(payload.duration, 0))
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


@app.post("/mouse/down")
def mouse_down(payload: MouseButtonRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    pyautogui.mouseDown(button=normalize_button(payload.button))
    return {"ok": True}


@app.post("/mouse/up")
def mouse_up(payload: MouseButtonRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    pyautogui.mouseUp(button=normalize_button(payload.button))
    return {"ok": True}


@app.post("/mouse/click")
def mouse_click(payload: MouseClickRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action(
        "mouse.click",
        {"button": payload.button, "clicks": payload.clicks, "interval": payload.interval},
    )


@app.post("/keyboard/down")
def key_down(payload: KeyRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("keyboard.down", {"key": payload.key})


@app.post("/keyboard/up")
def key_up(payload: KeyRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("keyboard.up", {"key": payload.key})


@app.post("/keyboard/press")
def key_press(payload: KeyRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("keyboard.press", {"key": payload.key})


@app.post("/keyboard/hotkey")
def hotkey(payload: HotkeyRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("keyboard.hotkey", {"keys": payload.keys})


@app.post("/keyboard/write")
def write_text(payload: WriteRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("keyboard.write", {"text": payload.text, "interval": payload.interval})


@app.post("/wait")
def wait_seconds(payload: WaitRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return execute_action("wait", {"seconds": payload.seconds})


@app.post("/roblox/open-game")
def roblox_open_game(payload: RobloxGameRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return open_roblox_game(payload.id)


@app.post("/file/open")
def open_file(payload: OpenFileRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
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
def get_files_under_folder(path: str, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    folder = resolve_path(path, "folder path")
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404, detail=f"Folder was not found: {folder}")

    files: list[str] = []
    for root, _, filenames in os.walk(folder, followlinks=False):
        for filename in filenames:
            files.append(os.path.join(root, filename))
            if len(files) >= 10000:
                return {"files": sorted(files, key=str.casefold), "truncated": True}

    return {"files": sorted(files, key=str.casefold), "truncated": False}


@app.get("/files/read")
def read_file(path: str, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
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
def write_file(payload: FileWriteRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
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
def create_folder(payload: FolderRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    folder = resolve_path(payload.path, "folder path")

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}") from exc

    return {"ok": True, "path": folder}


@app.post("/folders/destroy")
def destroy_folder(payload: FolderRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
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
def open_app(payload: OpenAppRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    return open_app_by_name(payload.name)


@app.post("/batch")
def run_batch(payload: BatchRequest, x_flowmacro_session: str | None = Header(default=None)) -> dict[str, Any]:
    require_session(x_flowmacro_session)
    results = [execute_action(action.type, action.payload) for action in payload.actions]
    return {"ok": True, "count": len(results), "results": results}


def open_cloudflare_tunnel(port: int) -> str | None:
    cloudflared = ensure_cloudflared()
    if not cloudflared:
        return None

    command = [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    url_holder = {"url": None}

    def collect() -> None:
        if not process.stdout:
            return
        for line in process.stdout:
            text = line.rstrip()
            if "trycloudflare.com" in text and "https://" in text and not url_holder["url"]:
                for token in line.split():
                    if token.startswith("https://") and "trycloudflare.com" in token:
                        url_holder["url"] = token
                        print(f"Cloudflare tunnel ready: {token}")
                        break
                continue

            lowered = text.lower()
            if any(word in lowered for word in ("error", "failed", "unable", "panic")):
                print(f"[cloudflared] {text}")

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()

    for _ in range(60):
        if url_holder["url"]:
            return url_holder["url"]
        if process.poll() is not None:
            break
        time.sleep(0.5)

    with suppress(Exception):
        process.terminate()
    return None


def start_tunnel(port: int) -> str | None:
    return open_cloudflare_tunnel(port)


def find_cloudflared_executable() -> str | None:
    cloudflared = shutil.which("cloudflared")
    if cloudflared:
        return cloudflared

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

        for root, _, files in os.walk(base):
            if "cloudflared.exe" in files:
                return os.path.join(root, "cloudflared.exe")

    return None


def install_cloudflared_windows() -> str | None:
    winget = shutil.which("winget")
    if not winget:
        print("cloudflared was not found and winget is unavailable, so auto-install could not run.")
        return None

    print("cloudflared was not found. Attempting automatic install with winget...")
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
        result = subprocess.run(command, check=False, text=True)
    except OSError as exc:
        print(f"Automatic cloudflared install failed to launch: {exc}")
        return None

    if result.returncode != 0:
        existing = find_cloudflared_executable()
        if existing:
            print("winget reported a non-success code, but cloudflared was found locally and will be used.")
            return existing
        print(f"Automatic cloudflared install failed with exit code {result.returncode}.")
        return None

    return find_cloudflared_executable()


def ensure_cloudflared() -> str | None:
    cloudflared = find_cloudflared_executable()
    if cloudflared:
        return cloudflared

    if platform.system() == "Windows":
        return install_cloudflared_windows()

    print("cloudflared was not found on PATH.")
    return None


def main() -> None:
    global EXTENSION_TEMPLATE

    parser = argparse.ArgumentParser(description="Host a local API for the PenguinMod extension.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--tunnel",
        choices=["cloudflare", "none"],
        default="cloudflare",
        help="Expose the local API over Cloudflare Tunnel if possible.",
    )
    args = parser.parse_args()
    EXTENSION_TEMPLATE = load_extension_template()

    public_url = None
    if args.tunnel == "cloudflare":
        public_url = start_tunnel(args.port)

    local_base_url = f"http://{args.host}:{args.port}"
    local_extension_url = f"{local_base_url}/extension/{SESSION_ID}.js"

    print(f"Local API: {local_base_url}")
    print(f"Session ID: {SESSION_ID}")
    print(f"Extension URL: {local_extension_url}")
    if public_url:
        print(f"Public API: {public_url}")
        print(f"Public extension URL: {public_url}/extension/{SESSION_ID}.js")
    else:
        print("No public tunnel could be opened. The local server will still start.")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
