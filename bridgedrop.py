"""BridgeDrop: send files and text between a Windows PC or Mac and phones or other computers on the local network."""

import argparse
import ctypes
import hmac
import html
import http.client
import http.server
import json
import mimetypes
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.cookies import SimpleCookie
from pathlib import Path

import qr

try:
    import winreg
except ImportError:
    winreg = None

__version__ = "1.0.0"

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
MAC_APP_BROWSERS = ("Google Chrome", "Microsoft Edge")

# A packaged app unpacks its bundled files elsewhere and reports the place in sys._MEIPASS.
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DEFAULT_PORT = 8765
COOKIE_NAME = "bd_token"
CHUNK_SIZE = 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
ITEM_TTL_SECONDS = 24 * 60 * 60
FREE_SPACE_MARGIN = 100 * 1024 * 1024
PROBE_HOST = "captive.apple.com"
PORTAL_SUCCESS = b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"
PUBLIC_ICONS = {
    "/icon-32.png": "icon-32.png", "/apple-touch-icon.png": "icon-touch.png",
    "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
}
# Android's "add to home screen" takes its icon from here. With no start_url the shortcut keeps the page's own address.
WEB_MANIFEST = json.dumps({
    "name": "BridgeDrop", "short_name": "BridgeDrop", "display": "browser",
    "icons": [{"src": f"/icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"} for size in (192, 512)],
}).encode("utf-8")
PORTAL_PASS_SECONDS = 30
SAFARI_HOLD_SECONDS = 15 * 60
PRESENCE_TIMEOUT_SECONDS = 10
WINDOW_CLOSE_GRACE_SECONDS = 10
EVENT_PING_SECONDS = 5
APP_WINDOW_SIZE = "--window-size=620,860"
PC_ID = "pc"
INLINE_TYPE = re.compile(r"^(image/(jpeg|png|gif|webp|heic|heif|bmp|avif)|video/(mp4|quicktime|webm)|audio/[\w.+-]+)$")
KIND_NAMES = {"iphone": "iPhone", "ipad": "iPad", "mac": "Mac", "android": "Android", "windows": "Windows PC", "other": "デバイス"}
HOSTS_ICS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts.ics"
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


VIEW_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>BridgeDrop</title>
<style>
:root { --bg: #f2f2f7; --card: #ffffff; --text: #1c1c1e; --muted: rgba(60, 60, 67, .66); --line: rgba(120, 120, 128, .12); --accent: #0a6cc4; --accent-text: #ffffff; --tint: #0a6cc4; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #000000; --card: #1c1c1e; --text: #ffffff; --muted: rgba(235, 235, 245, .66); --line: rgba(120, 120, 128, .28); --accent: #4aa8ff; --accent-text: #001a33; --tint: #4aa8ff; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); padding: 16px;
  -webkit-font-smoothing: antialiased; font: 16px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Hiragino Sans", "Segoe UI Variable Text", "Yu Gothic UI", "Meiryo", sans-serif; }
main { max-width: 680px; margin: 0 auto; display: grid; gap: 12px; }
a { display: flex; align-items: center; justify-content: center; min-height: 44px; border-radius: 12px; padding: 8px 12px;
  background: var(--line); color: var(--tint); text-decoration: none; font-size: 16px; font-weight: 500; }
a.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
h1 { font-size: 15px; font-weight: 600; margin: 0; overflow-wrap: anywhere; }
p { margin: 0; color: var(--muted); font-size: 14px; }
img, video { display: block; width: 100%; max-height: 70vh; object-fit: contain; background: var(--card); border-radius: 14px; }
</style>
</head>
<body>
<main>
  <a class="primary" href="{home_url}">BridgeDropに戻る</a>
  <h1>{name}</h1>
  {media}
  <p>{hint}</p>
  <a href="{file_url}">ファイルとして保存</a>
</main>
{start_save}
</body>
</html>
"""


def safe_filename(name):
    """Turn a client-supplied name into a single, valid Windows file name."""
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not name:
        name = "file"
    if name.split(".")[0].upper() in RESERVED_NAMES:
        name = "_" + name
    stem, ext = os.path.splitext(name)
    while len((stem + ext).encode("utf-8")) > 200 and stem:
        stem = stem[:-1]
    return (stem or "file") + ext[:40]


def clean_host_name(host):
    name = host.split(".")[0].strip()
    return None if not name or name.isdigit() else name


def lookup_device_name(ip, hosts_path=HOSTS_ICS):
    """The name a device gave the network, if any: Windows records it for hotspot clients."""
    try:
        for line in hosts_path.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split("#")[0].split()
            if len(fields) >= 2 and fields[0] == ip:
                return clean_host_name(fields[1])
    except OSError:
        pass
    try:
        return clean_host_name(socket.gethostbyaddr(ip)[0])
    except OSError:
        return None


class Store:
    """In-memory devices and transfers. Anything addressed to the PC is written to save_dir."""

    def __init__(self, save_dir, spool_dir):
        self.lock = threading.RLock()
        self.save_dir = save_dir
        self.spool_dir = spool_dir
        self.items = {}
        name = platform.node().removesuffix(".local")[:24] or "PC"
        kind = "mac" if IS_MAC else "windows" if IS_WINDOWS else "other"
        self.devices = {PC_ID: {"id": PC_ID, "name": name, "kind": kind, "named": True, "resolved": False}}
        self.connections = {}
        self.present = {}
        self.opened_by = {}
        self.safari_seen = {}
        self.listeners = set()
        save_dir.mkdir(parents=True, exist_ok=True)
        spool_dir.mkdir(parents=True, exist_ok=True)
        # The spool only holds files waiting in a previous run's in-memory queue.
        for path in spool_dir.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)

    def notify(self):
        with self.lock:
            for listener in self.listeners:
                listener.put(True)

    def touch_device(self, device_id, name=None, kind=None):
        with self.lock:
            device = self.devices.setdefault(
                device_id, {"id": device_id, "name": KIND_NAMES["other"], "kind": "other", "named": False, "resolved": False}
            )
            if device_id == PC_ID:
                return device
            if kind in KIND_NAMES:
                device["kind"] = kind
                if not device["named"] and not device["resolved"]:
                    device["name"] = KIND_NAMES[kind]
            if name:
                device["name"] = name[:40]
                device["named"] = True
            return device

    def resolve_name(self, device_id, ip):
        """Looked up off the request thread, because a reverse lookup can take seconds."""
        with self.lock:
            device = self.devices.get(device_id)
            if not device or device["named"] or device["resolved"]:
                return
            kind_name = KIND_NAMES.get(device["kind"], "")
        name = lookup_device_name(ip)
        if not name or name.casefold() == kind_name.casefold():
            return
        with self.lock:
            if not device["named"]:
                device["name"] = name[:40]
                device["resolved"] = True
        self.notify()

    def portal_passed(self, ip):
        """Whether iOS's connectivity check should pass instead of opening the sheet.

        Right after the page opened or while it is open, so iOS treats the Wi-Fi as normal, and for a good
        while after Safari was used, because someone in Safari does not want the sheet on top of it.
        """
        now = time.monotonic()
        with self.lock:
            if f"ip-{ip}" in self.present:
                return True
            opened = self.opened_by.get(ip)
            if opened is not None and now - opened < PORTAL_PASS_SECONDS:
                return True
            safari = self.safari_seen.get(ip)
            return safari is not None and now - safari < SAFARI_HOLD_SECONDS

    def note_browser(self, ip, user_agent):
        """The sheet iOS opens has no "Safari" in its user agent; Safari itself does."""
        if "Safari/" in user_agent:
            with self.lock:
                self.safari_seen[ip] = time.monotonic()

    def safari_open(self, ip):
        with self.lock:
            safari = self.safari_seen.get(ip)
        return safari is not None and time.monotonic() - safari < PRESENCE_TIMEOUT_SECONDS

    def forget_portal_passes(self):
        with self.lock:
            self.opened_by.clear()
            self.safari_seen.clear()

    def heartbeat(self, device_id):
        with self.lock:
            arrived = device_id not in self.present
            self.present[device_id] = time.monotonic()
        if arrived:
            self.notify()

    def leave(self, device_id):
        with self.lock:
            left = self.present.pop(device_id, None) is not None
        if left:
            self.notify()

    def expire_presence(self):
        cutoff = time.monotonic() - PRESENCE_TIMEOUT_SECONDS
        with self.lock:
            expired = [device_id for device_id, seen in self.present.items() if seen < cutoff]
            for device_id in expired:
                del self.present[device_id]
        if expired:
            self.notify()

    def connect(self, device_id, listener):
        with self.lock:
            self.connections[device_id] = self.connections.get(device_id, 0) + 1
            self.listeners.add(listener)
        self.notify()

    def disconnect(self, device_id, listener):
        with self.lock:
            self.connections[device_id] = max(0, self.connections.get(device_id, 1) - 1)
            self.listeners.discard(listener)
        self.notify()

    def state_for(self, device_id):
        with self.lock:
            # Only devices with the page open right now can be chosen, so a phone that left disappears at once.
            devices = [
                {**device, "online": True}
                for device_id_, device in self.devices.items()
                if device_id_ in (PC_ID, device_id) or device_id_ in self.present
            ]
            items = [
                self.public_item(item)
                for item in self.items.values()
                if device_id in (item["from"], item["to"])
            ]
        items.sort(key=lambda item: item["time"], reverse=True)
        return {"me": device_id, "devices": devices, "items": items, "version": __version__}

    def public_item(self, item):
        hidden = {"path"}
        public = {key: value for key, value in item.items() if key not in hidden}
        public["fromName"] = self.devices.get(item["from"], {}).get("name", "?")
        public["toName"] = self.devices.get(item["to"], {}).get("name", "?")
        return public

    def add_item(self, **fields):
        item = {"id": secrets.token_hex(8), "time": time.time(), "taken": False, "saved": False, **fields}
        with self.lock:
            self.items[item["id"]] = item
        self.notify()
        return item

    def unique_save_path(self, name):
        stem, ext = os.path.splitext(name)
        candidate = self.save_dir / name
        n = 1
        while candidate.exists():
            candidate = self.save_dir / f"{stem} ({n}){ext}"
            n += 1
        return candidate

    def remove_item(self, item_id):
        with self.lock:
            item = self.items.pop(item_id, None)
        # Files already delivered to the PC's save folder belong to the user: only spooled copies go.
        if item and item.get("path") and not item["saved"]:
            Path(item["path"]).unlink(missing_ok=True)
        self.notify()

    def expire_old_items(self):
        cutoff = time.time() - ITEM_TTL_SECONDS
        with self.lock:
            expired = [item["id"] for item in self.items.values() if item["time"] < cutoff]
        for item_id in expired:
            self.remove_item(item_id)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BridgeDrop"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    @property
    def store(self):
        return self.server.store

    @property
    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def parse(self):
        parts = urllib.parse.urlsplit(self.path)
        self.route = parts.path
        self.query = {key: values[0] for key, values in urllib.parse.parse_qs(parts.query).items()}

    def authorized(self):
        token = self.server.token
        supplied = self.query.get("t", "")
        if supplied and hmac.compare_digest(supplied.encode(), token.encode()):
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if COOKIE_NAME in cookie:
            return hmac.compare_digest(cookie[COOKIE_NAME].value.encode(), token.encode())
        return False

    def device_id(self):
        """The PC is the loopback connection; anything else is one device per address, however many tabs it opens."""
        return PC_ID if self.is_local else f"ip-{self.client_address[0]}"

    def send_body(self, status, body, content_type, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_body(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def reject(self, status, message):
        """Reply with an error and drop the connection, since a request body may be left unread."""
        self.close_connection = True
        self.send_body(status, json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", [("Connection", "close")])

    def note_browser(self):
        if not self.is_local:
            self.store.note_browser(self.client_address[0], self.headers.get("User-Agent", ""))

    def do_GET(self):
        self.parse()
        if self.route == "/healthz":
            return self.send_body(200, b"bridgedrop", "text/plain")
        if self.route == "/manifest.webmanifest":
            return self.send_body(200, WEB_MANIFEST, "application/manifest+json")
        if self.route in PUBLIC_ICONS:
            # Open without the token: phones fetch these on their own when adding the page to the home screen.
            return self.send_body(200, (APP_DIR / "assets" / PUBLIC_ICONS[self.route]).read_bytes(), "image/png")
        if not self.authorized():
            if self.route == "/":
                message = "PCのBridgeDrop画面に表示されたQRコードを読み取って開き直してください。"
                return self.send_body(401, f"<!doctype html><meta charset=utf-8><p>{message}</p>".encode("utf-8"),
                                      "text/html; charset=utf-8")
            return self.reject(401, "unauthorized")
        if self.route == "/":
            return self.serve_page()
        if self.route == "/api/events":
            return self.serve_events()
        if self.route == "/api/state":
            device_id = self.device_id()
            self.store.touch_device(device_id)
            state = self.store.state_for(device_id)
            if device_id == PC_ID:
                state["direct"] = self.server.direct.public()
            else:
                state["safariOpen"] = self.store.safari_open(self.client_address[0])
            return self.send_json(state)
        if self.route == "/api/pairing":
            return self.send_json(self.server.pairing())
        if self.route.startswith("/file/"):
            return self.serve_file(self.route[len("/file/"):])
        if self.route.startswith("/view/"):
            return self.serve_view(self.route[len("/view/"):])
        if self.route == "/zip":
            return self.serve_zip()
        if self.route == "/zip-view":
            return self.serve_zip_view()
        self.reject(404, "not found")

    do_HEAD = do_GET

    def do_POST(self):
        self.parse()
        if not self.authorized():
            return self.reject(401, "unauthorized")
        device_id = self.device_id()
        self.store.touch_device(device_id)
        if self.route == "/api/ping":
            self.store.heartbeat(device_id)
            self.note_browser()
            return self.send_json({"ok": True})
        if self.route == "/api/leave":
            self.store.leave(device_id)
            return self.send_json({"ok": True})
        if self.route == "/api/upload":
            return self.handle_upload(device_id)
        if self.route == "/api/text":
            return self.handle_text(device_id)
        if self.route == "/api/delete":
            return self.handle_delete(device_id)
        if self.route == "/api/rename":
            self.store.touch_device(device_id, name=self.query.get("name", "").strip())
            self.store.notify()
            return self.send_json({"ok": True})
        if self.route == "/api/reveal":
            return self.handle_reveal()
        if self.route == "/api/direct":
            return self.handle_direct()
        if self.route == "/api/quit":
            return self.handle_quit()
        self.reject(404, "not found")

    def handle_quit(self):
        if not self.is_local:
            return self.reject(403, "forbidden")
        self.send_json({"ok": True})
        self.wfile.flush()
        # shutdown() waits for serve_forever, which must not be blocked by this request's thread.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def handle_direct(self):
        if not self.is_local:
            return self.reject(403, "forbidden")
        action = self.query.get("action")
        if not IS_WINDOWS:
            return self.reject(501, "not supported on this system")
        if action == "start" and self.query.get("mode") in ("direct", "hotspot"):
            self.server.direct.start(self.query["mode"])
        elif action == "stop":
            self.server.direct.stop()
        else:
            return self.reject(400, "invalid action")
        self.send_json({"ok": True})

    def serve_page(self):
        if not self.is_local:
            # From now on the connectivity check passes, so iOS keeps this Wi-Fi as a normal network.
            self.store.opened_by[self.client_address[0]] = time.monotonic()
            self.note_browser()
        body = (APP_DIR / "index.html").read_bytes()
        cookie = f"{COOKIE_NAME}={self.server.token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        csp = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'"
        self.send_body(200, body, "text/html; charset=utf-8",
                       [("Set-Cookie", cookie), ("Content-Security-Policy", csp)])

    def serve_events(self):
        device_id = self.device_id()
        self.store.touch_device(device_id, name=self.query.get("name", "").strip(), kind=self.query.get("kind"))
        if not self.is_local:
            self.store.heartbeat(device_id)
            threading.Thread(target=self.store.resolve_name, args=(device_id, self.client_address[0]), daemon=True).start()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        listener = queue.Queue()
        self.store.connect(device_id, listener)
        try:
            self.wfile.write(b"data: change\n\n")
            self.wfile.flush()
            while True:
                try:
                    listener.get(timeout=EVENT_PING_SECONDS)
                    self.wfile.write(b"data: change\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.store.disconnect(device_id, listener)

    def read_target(self, device_id):
        to = self.query.get("to", "")
        with self.store.lock:
            known = to in self.store.devices
        if not known or to == device_id:
            return None
        return to

    def handle_upload(self, device_id):
        to = self.read_target(device_id)
        if not to:
            return self.reject(400, "invalid destination")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.reject(411, "length required")
        if length < 0:
            return self.reject(400, "invalid length")

        name = safe_filename(self.query.get("name", ""))
        item_id = secrets.token_hex(8)
        to_pc = to == PC_ID
        directory = self.store.save_dir if to_pc else self.store.spool_dir
        if shutil.disk_usage(directory).free < length + FREE_SPACE_MARGIN:
            return self.reject(507, "PCの空き容量が足りません")

        part = directory / f".{item_id}.part"
        self.connection.settimeout(120)
        try:
            remaining = length
            with open(part, "xb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    out.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            part.unlink(missing_ok=True)
            self.close_connection = True
            return
        finally:
            self.connection.settimeout(None)

        with self.store.lock:
            final = self.store.unique_save_path(name) if to_pc else directory / item_id
            part.replace(final)
        mime = self.query.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        item = self.store.add_item(kind="file", name=final.name if to_pc else name, size=length, mime=mime,
                                   path=str(final), saved=to_pc, taken=to_pc, to=to, **{"from": device_id})
        self.send_json({"id": item["id"]})

    def handle_text(self, device_id):
        to = self.read_target(device_id)
        if not to:
            return self.reject(400, "invalid destination")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.reject(411, "length required")
        if not 0 < length <= MAX_TEXT_BYTES:
            return self.reject(413, "テキストが長すぎます")
        text = self.rfile.read(length).decode("utf-8", "replace")
        item = self.store.add_item(kind="text", text=text, name="テキスト", size=length, to=to, **{"from": device_id})
        self.send_json({"id": item["id"]})

    def handle_delete(self, device_id):
        with self.store.lock:
            item = self.store.items.get(self.query.get("id", ""))
            allowed = item and device_id in (item["from"], item["to"])
        if not allowed:
            return self.reject(404, "not found")
        self.store.remove_item(item["id"])
        self.send_json({"ok": True})

    def handle_reveal(self):
        if not self.is_local:
            return self.reject(403, "forbidden")
        with self.store.lock:
            item = self.store.items.get(self.query.get("id", ""))
            path = Path(item["path"]) if item and item["saved"] else None
        reveal_in_file_manager(path if path and path.exists() else None, self.store.save_dir)
        self.send_json({"ok": True})

    def serve_view(self, item_id):
        """A received file on a page of its own, with a way back to BridgeDrop."""
        self.note_browser()
        with self.store.lock:
            item = self.store.items.get(item_id)
        device_id = self.device_id()
        if not item or item["kind"] != "file" or device_id not in (item["from"], item["to"]):
            return self.reject(404, "not found")

        token = urllib.parse.quote(self.server.token)
        file_url = f"/file/{item_id}?t={token}"
        media_url = html.escape(f"{file_url}&inline=1&opened=1")
        name = html.escape(item["name"])
        media = ""
        hint = "下のボタンで保存できます。"
        android = "Android" in self.headers.get("User-Agent", "")
        if INLINE_TYPE.match(item["mime"]) and item["mime"].startswith("image/"):
            media = f'<img src="{media_url}" alt="{name}">'
            hint = ("画像を長押しして「画像をダウンロード」を選ぶと保存できます。" if android
                    else "画像を長押しして「写真に保存」を選ぶと、写真アプリに保存できます。")
        elif INLINE_TYPE.match(item["mime"]) and item["mime"].startswith("video/"):
            media = f'<video src="{media_url}" controls playsinline></video>'
            hint = ("下のボタンで保存できます。" if android
                    else "動画を写真アプリに入れるには、保存したあと「ファイル」アプリの共有メニューから「ビデオを保存」を選びます。")
        self.send_view_page(name, media, hint, file_url, self.query.get("save") == "1")

    def send_view_page(self, name, media, hint, file_url, auto_save):
        token = urllib.parse.quote(self.server.token)
        start_save = f"<script>location.href={json.dumps(file_url)};</script>" if auto_save else ""
        page = (
            VIEW_PAGE.replace("{name}", name).replace("{media}", media).replace("{hint}", html.escape(hint))
            .replace("{file_url}", html.escape(file_url)).replace("{home_url}", html.escape(f"/?t={token}"))
            .replace("{start_save}", start_save)
        )
        csp ="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; media-src 'self'"
        self.send_body(200, page.encode("utf-8"), "text/html; charset=utf-8", [
            ("Set-Cookie", f"{COOKIE_NAME}={self.server.token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"),
            ("Content-Security-Policy", csp),
        ])

    def items_to_bundle(self):
        """The requested files addressed to this device: only those it could download one by one anyway."""
        device_id = self.device_id()
        with self.store.lock:
            items = [self.store.items.get(item_id) for item_id in self.query.get("ids", "").split(",")]
        return [
            item for item in items
            if item and item["kind"] == "file" and item["to"] == device_id and Path(item["path"]).is_file()
        ]

    def serve_zip_view(self):
        self.note_browser()
        items = self.items_to_bundle()
        if not items:
            return self.reject(404, "not found")
        ids = ",".join(item["id"] for item in items)
        file_url = f"/zip?ids={ids}&t={urllib.parse.quote(self.server.token)}"
        hint = "保存したZIPは、ファイルを管理するアプリから開いて展開できます。"
        self.send_view_page(f"{len(items)}件のファイル", "", hint, file_url, True)

    def serve_zip(self):
        if self.command == "HEAD":
            return self.reject(405, "method not allowed")
        items = self.items_to_bundle()
        if not items:
            return self.reject(404, "not found")
        spool = self.store.spool_dir
        total = sum(Path(item["path"]).stat().st_size for item in items)
        if shutil.disk_usage(spool).free < total + FREE_SPACE_MARGIN:
            return self.reject(507, "PCの空き容量が足りません")

        temp = spool / f".{secrets.token_hex(8)}.zip"
        try:
            used = set()
            # Already compressed media dominates here, so storing is both faster and about as small.
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED, allowZip64=True) as bundle:
                for item in items:
                    stem, ext = os.path.splitext(item["name"])
                    name, n = item["name"], 1
                    while name.casefold() in used:
                        name = f"{stem} ({n}){ext}"
                        n += 1
                    used.add(name.casefold())
                    bundle.write(item["path"], arcname=name)

            filename = time.strftime("BridgeDrop_%Y%m%d_%H%M%S.zip")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(temp.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(temp, "rb") as source:
                shutil.copyfileobj(source, self.wfile, CHUNK_SIZE)
        finally:
            temp.unlink(missing_ok=True)

        with self.store.lock:
            for item in items:
                item["taken"] = True
        self.store.notify()

    def serve_file(self, item_id):
        with self.store.lock:
            item = self.store.items.get(item_id)
        device_id = self.device_id()
        if not item or item["kind"] != "file" or device_id not in (item["from"], item["to"]):
            return self.reject(404, "not found")
        path = Path(item["path"])
        if not path.is_file():
            return self.reject(404, "file is gone")

        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206

        inline = self.query.get("inline") == "1" and INLINE_TYPE.match(item["mime"])
        mime = item["mime"] if inline else "application/octet-stream"
        quoted = urllib.parse.quote(item["name"])
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quoted}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return

        with open(path, "rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

        # Thumbnails also load inline, so only an explicit open counts as picking the file up.
        picked_up = not inline or self.query.get("opened") == "1"
        if picked_up and device_id == item["to"] and not item["taken"] and end == size - 1:
            with self.store.lock:
                item["taken"] = True
            self.store.notify()


def wifi_qr_text(ssid, passphrase):
    escape = lambda value: re.sub(r'([\\;,:"])', r"\\\1", value)
    return f"WIFI:T:WPA;S:{escape(ssid)};P:{escape(passphrase)};;"


def qr_or_none(text):
    try:
        return qr.to_svg(text)
    except ValueError:
        return None


class PortalHandler(http.server.BaseHTTPRequestHandler):
    """Answers iOS's connectivity check so that joining the Wi-Fi opens BridgeDrop by itself."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.server.store.portal_passed(self.client_address[0]):
            body = PORTAL_SUCCESS
        else:
            url = self.server.url
            body = (f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">'
                    f'<a href="{url}">BridgeDropを開く</a>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_HEAD = do_GET


class QuickBindServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTPServer looks up this machine's full name while binding, which leaves a Mac unreachable for many seconds."""

    daemon_threads = True
    # Windows would otherwise let a second copy bind the same port silently.
    allow_reuse_address = False

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class PortalServer(QuickBindServer):
    pass


class CaptivePortal:
    """While the link is up: resolves the name iOS probes to this PC and relays all other DNS unchanged."""

    def __init__(self, ip, url, store, upstreams):
        self.ip = ip
        self.upstreams = upstreams
        self.running = True
        self.dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Windows' own resolver holds only the wildcard 0.0.0.0:53, so the link's address is free to take.
            self.dns.bind((ip, 53))
            self.dns.settimeout(1)
            self.http = PortalServer((ip, 80), PortalHandler)
        except OSError:
            self.dns.close()
            raise
        self.http.url = url
        self.http.store = store
        threading.Thread(target=self.serve_dns, daemon=True).start()
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def serve_dns(self):
        while self.running:
            try:
                query, client = self.dns.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self.answer, args=(query, client), daemon=True).start()

    def answer(self, query, client):
        response = self.local_answer(query) or self.relay(query)
        if response:
            try:
                self.dns.sendto(response, client)
            except OSError:
                pass

    def local_answer(self, query):
        labels, offset = [], 12
        try:
            while query[offset]:
                length = query[offset]
                labels.append(query[offset + 1:offset + 1 + length].decode("ascii").lower())
                offset += length + 1
            qtype = int.from_bytes(query[offset + 1:offset + 3], "big")
        except (IndexError, UnicodeDecodeError):
            return None
        if ".".join(labels) != PROBE_HOST:
            return None
        question = query[12:offset + 5]
        answers = 1 if qtype == 1 else 0
        header = query[:2] + b"\x81\x80\x00\x01" + answers.to_bytes(2, "big") + b"\x00\x00\x00\x00"
        record = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x05\x00\x04" + socket.inet_aton(self.ip) if answers else b""
        return header + question + record

    def relay(self, query):
        for server in self.upstreams:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                upstream.settimeout(3)
                try:
                    upstream.sendto(query, (server, 53))
                    return upstream.recv(4096)
                except OSError:
                    continue
        return None

    def close(self):
        self.running = False
        self.dns.close()
        self.http.shutdown()
        self.http.server_close()


class DirectLink:
    """Runs direct_link.ps1 and tracks the router-less Wi-Fi link it keeps up."""

    def __init__(self, data_dir, store, url_for):
        self.credentials_path = data_dir / "direct.json"
        self.store = store
        self.notify = store.notify
        self.url_for = url_for
        self.lock = threading.Lock()
        self.proc = None
        self.portal = None
        # Starting a Wi-Fi link from here is Windows only. Elsewhere the computer joins a phone's tethering instead.
        self.idle = {"status": "off" if IS_WINDOWS else "unsupported"}
        self.info = dict(self.idle)

    def open_portal(self, proc, ip, url, upstreams):
        # Without a DNS server to relay to, taking over DNS would cut the phone's internet.
        if not upstreams:
            return False
        try:
            portal = CaptivePortal(ip, url, self.store, upstreams)
        except OSError:
            return False
        with self.lock:
            stale = self.proc is not proc
            if not stale:
                self.portal = portal
        if stale:
            portal.close()
            return False
        return True

    def close_portal(self):
        with self.lock:
            portal, self.portal = self.portal, None
        if portal:
            portal.close()
        self.store.forget_portal_passes()

    def public(self):
        with self.lock:
            return dict(self.info)

    def credentials(self):
        """Stable across runs, so the phone can rejoin a network it already knows."""
        try:
            saved = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            if saved.get("ssid") and saved.get("passphrase"):
                return saved
        except (OSError, ValueError):
            pass
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        saved = {
            "ssid": "DIRECT-BridgeDrop-" + "".join(secrets.choice(alphabet) for _ in range(4)),
            "passphrase": "".join(secrets.choice(alphabet) for _ in range(16)),
        }
        self.credentials_path.write_text(json.dumps(saved), encoding="utf-8")
        return saved

    def start(self, mode):
        self.stop()
        credentials = self.credentials()
        env = {**os.environ, "BD_SSID": credentials["ssid"], "BD_PASS": credentials["passphrase"]}
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(APP_DIR / "direct_link.ps1"), "-Mode", mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
        )
        with self.lock:
            self.proc = proc
            self.info = {"status": "starting", "mode": mode}
        self.notify()
        threading.Thread(target=self.watch, args=(proc, mode), daemon=True).start()

    def watch(self, proc, mode):
        ended_with = {"status": "off"}
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("status") == "on":
                url = self.url_for(message["ip"])
                wifi_text = wifi_qr_text(message["ssid"], message["passphrase"])
                upstreams = message.pop("dns", None) or []
                portal = mode == "hotspot" and self.open_portal(proc, message["ip"], url, upstreams)
                self.update(proc, {**message, "url": url, "wifiQr": qr_or_none(wifi_text), "urlQr": qr_or_none(url), "portal": portal})
            elif message.get("status") == "error":
                ended_with = {"status": "error", "mode": mode, "code": message.get("code")}
            elif "clients" in message:
                info = self.public()
                # A phone that left must get the automatic page again when it rejoins.
                if message["clients"] < info.get("clients", 0):
                    self.store.forget_portal_passes()
                self.update(proc, {**info, "clients": message["clients"]})
        proc.wait()
        with self.lock:
            current = self.proc is proc
        if current:
            self.close_portal()
        self.update(proc, ended_with)

    def update(self, proc, info):
        with self.lock:
            if self.proc is not proc:
                return
            self.info = info
        self.notify()

    def stop(self):
        self.close_portal()
        with self.lock:
            proc, self.proc = self.proc, None
            self.info = dict(self.idle)
        if proc:
            try:
                proc.stdin.close()
                proc.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        self.notify()


class Server(QuickBindServer):
    def __init__(self, address, store, token, data_dir):
        super().__init__(address, Handler)
        self.store = store
        self.token = token
        self.direct = DirectLink(data_dir, store, self.url_for)

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)

    def url_for(self, host):
        return f"http://{host}:{self.server_address[1]}/?t={self.token}"

    def pairing(self):
        return {
            "addresses": [
                {"ip": ip, "url": self.url_for(ip), "qr": qr.to_svg(self.url_for(ip))}
                for ip in local_addresses()
            ]
        }


def parse_ifconfig(output):
    return re.findall(r"^\s*inet (\d+\.\d+\.\d+\.\d+) ", output, flags=re.M)


def local_addresses():
    """IPv4 addresses of this PC, the one with the default route first."""
    addresses = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # No packet is sent; this only selects the route.
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass
    if IS_MAC:
        # Resolving a Mac's own host name can block for many seconds, and ifconfig lists every address anyway.
        try:
            addresses += parse_ifconfig(subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            addresses += socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:
            pass
    # 192.168.137.x is the direct link's own subnet, which has its own QR codes.
    usable = [a for a in addresses if not a.startswith(("127.", "169.254.", "0.", "192.168.137."))]
    return list(dict.fromkeys(usable))


def load_token(data_dir, renew):
    path = data_dir / "token.txt"
    if not renew and path.exists():
        token = path.read_text(encoding="ascii").strip()
        if token:
            return token
    token = secrets.token_urlsafe(16)
    path.write_text(token, encoding="ascii")
    return token


def already_running(port):
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/healthz")
        return conn.getresponse().read() == b"bridgedrop"
    except OSError:
        return False


def sweep(store):
    while True:
        time.sleep(600)
        store.expire_old_items()


def watch_presence(store):
    while True:
        time.sleep(2)
        store.expire_presence()


def reveal_in_file_manager(path, folder):
    if IS_WINDOWS:
        subprocess.Popen(["explorer", f"/select,{path}"] if path else ["explorer", str(folder)])
    elif IS_MAC:
        subprocess.Popen(["open", "-R", str(path)] if path else ["open", str(folder)])


def find_app_browser():
    """Chrome, or else Edge: both can show a page in its own window without browser controls."""
    if not winreg:
        return None
    for exe in ("chrome.exe", "msedge.exe"):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
            try:
                path = winreg.QueryValue(root, key)
            except OSError:
                continue
            if os.path.isfile(path):
                return path
    return None


def find_mac_app_browser():
    for name in MAC_APP_BROWSERS:
        if any(Path(root, f"{name}.app").is_dir() for root in ("/Applications", Path.home() / "Applications")):
            return name
    return None


def open_app_window(url):
    if IS_MAC:
        name = find_mac_app_browser()
        if name:
            subprocess.Popen(["open", "-na", name, "--args", f"--app={url}", APP_WINDOW_SIZE])
            return
    else:
        browser = find_app_browser()
        if browser:
            subprocess.Popen([browser, f"--app={url}", APP_WINDOW_SIZE])
            return
    webbrowser.open(url)


def show_fatal_error(message):
    if IS_WINDOWS:
        ctypes.windll.user32.MessageBoxW(None, message, "BridgeDrop", 0x10)
    else:
        print(message, file=sys.stderr)


def quit_when_window_closes(server, store):
    """Closing the last PC window ends the app, after a grace period that lets a reload reconnect."""
    last_open = None
    while True:
        time.sleep(2)
        with store.lock:
            is_open = store.connections.get(PC_ID, 0) > 0
        if is_open:
            last_open = time.monotonic()
        elif last_open is not None and time.monotonic() - last_open > WINDOW_CLOSE_GRACE_SECONDS:
            server.shutdown()
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--save-dir", type=Path, default=Path.home() / "Downloads" / "BridgeDrop",
                        help="folder where files sent to this PC are saved")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".bridgedrop",
                        help="folder for the pairing token and files waiting to be picked up")
    parser.add_argument("--new-token", action="store_true", help="revoke paired devices by issuing a new QR code")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    # There is no stdout when started without a console.
    if sys.stdout:
        sys.stdout.reconfigure(errors="replace")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    token = load_token(args.data_dir, args.new_token)

    server = None
    for port in range(args.port, args.port + 10):
        if already_running(port):
            print("BridgeDrop はすでに起動しています。")
            if not args.no_browser:
                open_app_window(f"http://127.0.0.1:{port}/?t={token}")
            return
        try:
            store = Store(args.save_dir, args.data_dir / "spool")
            server = Server(("0.0.0.0", port), store, token, args.data_dir)
            break
        except OSError:
            continue
    if server is None:
        show_fatal_error("使えるポートが見つかりませんでした。")
        sys.exit(1)

    threading.Thread(target=sweep, args=(store,), daemon=True).start()
    threading.Thread(target=watch_presence, args=(store,), daemon=True).start()
    local_url = server.url_for("127.0.0.1")
    print(f"PC の画面: {local_url}")
    print(f"受信フォルダ: {args.save_dir}")
    if not args.no_browser:
        threading.Thread(target=quit_when_window_closes, args=(server, store), daemon=True).start()
        open_app_window(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.direct.stop()


if __name__ == "__main__":
    main()
