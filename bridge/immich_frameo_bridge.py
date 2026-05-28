#!/usr/bin/env python3
"""Tiny old-browser-safe Immich slideshow bridge for an Android/Frameo picture frame.

Serves no Immich API key to the frame. The frame only sees this local proxy.
Default source: curated albums/faces. Set IMMICH_API_KEY and IMMICH_BASE; no API key is served to the frame.
"""
from __future__ import annotations

import html
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IMMICH_BASE = os.environ.get("IMMICH_BASE", "http://127.0.0.1:2283").rstrip("/")
HOST = os.environ.get("FRAMEO_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("FRAMEO_BRIDGE_PORT", "8099"))
SLIDE_SECONDS = int(os.environ.get("FRAMEO_SLIDE_SECONDS", "45"))
CACHE_SECONDS = int(os.environ.get("FRAMEO_CACHE_SECONDS", "600"))
SOURCE = os.environ.get("FRAMEO_SOURCE", "albums_faces").strip().lower()
PAGE_SIZE = int(os.environ.get("FRAMEO_PAGE_SIZE", "1000"))
ALBUM_NAMES = [x.strip().lower() for x in os.environ.get("FRAMEO_ALBUM_NAMES", "").split(",") if x.strip()]
ALBUM_IDS = [x.strip() for x in os.environ.get("FRAMEO_ALBUM_IDS", "").split(",") if x.strip()]
PERSON_NAMES = [x.strip().lower() for x in os.environ.get("FRAMEO_PERSON_NAMES", "").split(",") if x.strip()]
PERSON_IDS = [x.strip() for x in os.environ.get("FRAMEO_PERSON_IDS", "").split(",") if x.strip()]
VAULT = os.path.expanduser(os.environ.get("FRAMEO_VAULT", ""))
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "").strip()
IMMICH_API_KEY_NAME = os.environ.get("IMMICH_API_KEY_NAME", "immich.api_key").strip() or "immich.api_key"
IMMICH_API_KEY_NAMES = [x.strip() for x in os.environ.get("IMMICH_API_KEY_NAMES", IMMICH_API_KEY_NAME).split(",") if x.strip()]
FRAME_CACHE_DIR = os.path.expanduser(os.environ.get("FRAMEO_IMAGE_CACHE_DIR", "~/.hermes/cache/immich-frameo/fitted"))
PREFETCH_COUNT = int(os.environ.get("FRAMEO_PREFETCH_COUNT", "5"))

_key_cache: dict[str, str] = {}
_asset_cache = {"when": 0.0, "mode": "unknown", "items": [], "asset_keys": {}}
_asset_lock = threading.Lock()
_prefetch_thread: threading.Thread | None = None
_history: list[str] = []
_history_index = -1


def get_key(key_name: str | None = None) -> str:
    # Generic installs usually use one API key from the environment. If set,
    # use it for all requests and do not require a vault. Multiple account/key
    # setups can opt into FRAMEO_VAULT + IMMICH_API_KEY_NAMES.
    if IMMICH_API_KEY:
        return IMMICH_API_KEY
    key_name = (key_name or IMMICH_API_KEY_NAME).strip() or IMMICH_API_KEY_NAME
    if key_name in _key_cache:
        return _key_cache[key_name]
    try:
        if not VAULT:
            raise RuntimeError("set IMMICH_API_KEY, or FRAMEO_VAULT plus IMMICH_API_KEY_NAME")
        out = subprocess.check_output([sys.executable, VAULT, "get", key_name], text=True, stderr=subprocess.DEVNULL, timeout=10)
        key = out.strip().splitlines()[-1].strip()
        if key:
            _key_cache[key_name] = key
            return key
    except Exception:
        pass
    raise RuntimeError(f"Could not load {key_name} from vault")


def immich_json(path: str, body: dict | None = None, key_name: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        IMMICH_BASE + path,
        data=data,
        headers={"x-api-key": get_key(key_name), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def selected_album_ids(key_name: str | None = None) -> list[str]:
    albums = immich_json("/api/albums", key_name=key_name)
    ids: list[str] = []
    wanted_ids = set(ALBUM_IDS)
    wanted_names = set(ALBUM_NAMES)
    for album in albums:
        album_id = album.get("id")
        name = (album.get("albumName") or "").strip().lower()
        if not album_id or int(album.get("assetCount") or 0) <= 0:
            continue
        if wanted_ids or wanted_names:
            if album_id in wanted_ids or name in wanted_names:
                ids.append(album_id)
        else:
            ids.append(album_id)
    return ids


def selected_person_ids(key_name: str | None = None) -> list[str]:
    doc = immich_json("/api/people", key_name=key_name)
    people = doc.get("people", []) if isinstance(doc, dict) else doc
    ids: list[str] = []
    wanted_ids = set(PERSON_IDS)
    wanted_names = set(PERSON_NAMES)
    for person in people:
        person_id = person.get("id")
        name = (person.get("name") or "").strip()
        lname = name.lower()
        if not person_id or person.get("isHidden") or not name:
            continue
        if wanted_ids or wanted_names:
            if person_id in wanted_ids or lname in wanted_names:
                ids.append(person_id)
        else:
            ids.append(person_id)
    return ids


def refresh_assets(force: bool = False) -> tuple[str, list[str]]:
    now = time.time()
    if not force and _asset_cache["items"] and now - _asset_cache["when"] < CACHE_SECONDS:
        return _asset_cache["mode"], list(_asset_cache["items"])

    def search_all(body: dict, key_name: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        page = 1
        while True:
            request_body = dict(body)
            request_body.update({"type": "IMAGE", "size": PAGE_SIZE, "page": page})
            doc = immich_json("/api/search/metadata", request_body, key_name=key_name)
            assets = doc.get("assets", {})
            items = assets.get("items", [])
            for item in items:
                asset_id = item.get("id")
                if asset_id and item.get("type") == "IMAGE" and asset_id not in seen:
                    ids.append(asset_id)
                    seen.add(asset_id)
            next_page = assets.get("nextPage")
            if not next_page:
                break
            page = next_page
        return ids

    def merge_searches(search_bodies: list[dict], key_name: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for body in search_bodies:
            for asset_id in search_all(body, key_name):
                if asset_id not in seen:
                    ids.append(asset_id)
                    seen.add(asset_id)
        return ids

    all_ids: list[str] = []
    asset_keys: dict[str, str] = {}
    seen_global: set[str] = set()
    mode_parts: list[str] = []
    key_names = IMMICH_API_KEY_NAMES or [IMMICH_API_KEY_NAME]

    for key_name in key_names:
        try:
            if SOURCE == "favorites":
                submode = "favorites"
                ids = search_all({"isFavorite": True}, key_name)
            elif SOURCE in ("albums", "album"):
                album_ids = selected_album_ids(key_name)
                submode = f"albums:{len(album_ids)}"
                ids = merge_searches([{"albumIds": [album_id]} for album_id in album_ids], key_name)
            elif SOURCE in ("faces", "people", "known_faces", "known-faces"):
                person_ids = selected_person_ids(key_name)
                submode = f"known_faces:{len(person_ids)}"
                ids = merge_searches([{"personIds": [person_id]} for person_id in person_ids], key_name)
            elif SOURCE in ("albums_faces", "albums+faces", "albums-and-faces", "curated"):
                album_ids = selected_album_ids(key_name)
                person_ids = selected_person_ids(key_name)
                submode = f"albums_faces:{len(album_ids)}albums:{len(person_ids)}faces"
                ids = merge_searches(
                    [{"albumIds": [album_id]} for album_id in album_ids]
                    + [{"personIds": [person_id]} for person_id in person_ids],
                    key_name,
                )
            else:
                submode = "all"
                ids = search_all({}, key_name)
        except Exception as e:
            sys.stderr.write("asset refresh failed for %s: %s: %s\n" % (key_name, type(e).__name__, e))
            mode_parts.append(f"{key_name}:error")
            continue
        mode_parts.append(f"{key_name}:{submode}:{len(ids)}")
        for asset_id in ids:
            if asset_id not in seen_global:
                seen_global.add(asset_id)
                all_ids.append(asset_id)
                asset_keys[asset_id] = key_name

    if len(key_names) == 1 and mode_parts:
        # Preserve the compact old status shape for single-user mode.
        bits = mode_parts[0].split(":", 1)
        mode = bits[1].rsplit(":", 1)[0] if len(bits) == 2 else mode_parts[0]
    else:
        mode = "multi:" + ";".join(mode_parts)
    random.shuffle(all_ids)
    with _asset_lock:
        _asset_cache.update({"when": now, "mode": mode, "items": all_ids, "asset_keys": asset_keys})
    start_prefetch()
    return mode, list(all_ids)


def fit_to_frame(data: bytes) -> bytes:
    """Return a 1280x800 letterboxed JPEG so the frame shows the whole photo."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-vf", "scale=1280:800:force_original_aspect_ratio=decrease,pad=1280:800:(ow-iw)/2:(oh-ih)/2:black",
        "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1",
    ]
    try:
        p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if p.returncode == 0 and p.stdout.startswith(b"\xff\xd8"):
            return p.stdout
        sys.stderr.write("ffmpeg frame fit failed: %s\n" % p.stderr.decode("utf-8", "replace")[:500])
    except Exception as e:
        sys.stderr.write("ffmpeg frame fit exception: %s: %s\n" % (type(e).__name__, e))
    return data


def cache_path(asset_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in asset_id)
    return os.path.join(FRAME_CACHE_DIR, safe + ".jpg")


def fetch_preview(asset_id: str, key_name: str | None = None) -> tuple[bytes, str, str]:
    """Fetch a fitted JPEG, using a disk cache so the frame is not held hostage by ffmpeg every slide."""
    os.makedirs(FRAME_CACHE_DIR, exist_ok=True)
    path = cache_path(asset_id)
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data.startswith(b"\xff\xd8"):
            return data, "image/jpeg", "hit"
    except FileNotFoundError:
        pass
    except Exception as e:
        sys.stderr.write("frame cache read failed for %s: %s\n" % (asset_id, e))

    # JPEG preview is friendlier to prehistoric Android browsers than Immich's thumbnail WEBP.
    url = f"{IMMICH_BASE}/api/assets/{urllib.parse.quote(asset_id)}/thumbnail?size=preview"
    req = urllib.request.Request(url, headers={"x-api-key": get_key(key_name)})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = fit_to_frame(r.read())
    if data.startswith(b"\xff\xd8"):
        tmp = path + ".tmp.%s" % os.getpid()
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception as e:
            sys.stderr.write("frame cache write failed for %s: %s\n" % (asset_id, e))
            try:
                os.unlink(tmp)
            except Exception:
                pass
    return data, "image/jpeg", "miss"


def start_prefetch() -> None:
    """Warm the next few fitted images in the background. Tiny luxury. Massive sanity."""
    global _prefetch_thread
    if PREFETCH_COUNT <= 0:
        return
    with _asset_lock:
        if _prefetch_thread and _prefetch_thread.is_alive():
            return
        candidates = [asset_id for asset_id in _asset_cache.get("items", []) if not os.path.exists(cache_path(asset_id))][:PREFETCH_COUNT]
        asset_keys = dict(_asset_cache.get("asset_keys", {}))
        if not candidates:
            return

    def worker(asset_ids: list[str]) -> None:
        for asset_id in asset_ids:
            try:
                fetch_preview(asset_id, asset_keys.get(asset_id))
            except Exception as e:
                sys.stderr.write("prefetch failed for %s: %s: %s\n" % (asset_id, type(e).__name__, e))

    _prefetch_thread = threading.Thread(target=worker, args=(candidates,), daemon=True)
    _prefetch_thread.start()


def next_asset(ids: list[str]) -> str | None:
    if not ids:
        return None
    with _asset_lock:
        items = _asset_cache.get("items", [])
        if not items:
            return random.choice(ids)
        window = max(PREFETCH_COUNT * 2, 1)
        selected_idx = 0
        for idx, asset_id in enumerate(items[:window]):
            if os.path.exists(cache_path(asset_id)):
                selected_idx = idx
                break
        asset_id = items.pop(selected_idx)
        items.append(asset_id)
        return asset_id


def select_asset(direction: str, ids: list[str]) -> str | None:
    """Return an asset id for next/prev navigation with a tiny global history for the one frame."""
    global _history_index
    direction = (direction or "next").lower()
    with _asset_lock:
        if direction in ("prev", "previous", "back") and _history and _history_index > 0:
            _history_index -= 1
            return _history[_history_index]
        if direction == "current" and _history and 0 <= _history_index < len(_history):
            return _history[_history_index]
        if direction in ("next", "forward") and _history and _history_index < len(_history) - 1:
            _history_index += 1
            return _history[_history_index]

    asset_id = next_asset(ids)
    if not asset_id:
        return None
    with _asset_lock:
        if _history_index < len(_history) - 1:
            del _history[_history_index + 1:]
        _history.append(asset_id)
        # Keep history bounded; this is a photo frame, not the Library of Alexandria.
        if len(_history) > 100:
            del _history[:-100]
        _history_index = len(_history) - 1
    return asset_id


class Handler(BaseHTTPRequestHandler):
    server_version = "ImmichFrameoBridge/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_bytes(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path in ("/", "/frame", "/frameo"):
                mode, ids = refresh_assets()
                status = f"Immich {html.escape(mode)} · {len(ids)} photos · {SLIDE_SECONDS}s · swipe up for status"
                body = f"""<!doctype html>
<html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,user-scalable=no'>
<title>Immich Frame</title>
<style>
html,body{{margin:0;width:1280px;height:800px;background:#000;overflow:hidden;font-family:Arial,sans-serif;}}
#wrap{{position:absolute;left:0;top:0;width:1280px;height:800px;background:#000;overflow:hidden;}}
#photo{{position:absolute;display:block;left:0;top:0;width:1280px;height:800px;border:0;background:#000;}}
#overlay{{position:absolute;left:0;bottom:0;width:1280px;height:46px;background:rgba(0,0,0,.45);opacity:0;color:#ddd;font-size:14px;}}
#overlay.active{{opacity:.88;}}
#badge{{position:absolute;right:10px;bottom:8px;padding:4px 8px;}}
#state{{position:absolute;left:10px;bottom:8px;padding:4px 8px;}}
#hit{{position:absolute;left:0;top:0;width:1280px;height:800px;background:rgba(0,0,0,0);}}
</style>
<script>
(function(){{
  var slideMs = {SLIDE_SECONDS * 1000};
  var timer = null;
  var retryTimer = null;
  var paused = false;
  var loading = false;
  var seq = 0;
  function byId(id){{ return document.getElementById(id); }}
  function setState(txt){{ var el = byId('state'); if (el) el.innerHTML = txt; }}
  function clearTimers(){{ if (timer) window.clearTimeout(timer); if (retryTimer) window.clearTimeout(retryTimer); timer = null; retryTimer = null; }}
  function scheduleNext(){{
    if (paused) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(function(){{ requestAsset('next'); }}, slideMs);
  }}
  function hasVisiblePixels(img){{
    if (!img || !img.complete || !img.naturalWidth || !img.naturalHeight) return false;
    try {{
      var canvas = document.createElement('canvas');
      canvas.width = 16; canvas.height = 10;
      var ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, 16, 10);
      var data = ctx.getImageData(0, 0, 16, 10).data;
      var visible = 0;
      for (var i = 0; i < data.length; i += 4) {{
        if (data[i+3] > 0 && (data[i] > 10 || data[i+1] > 10 || data[i+2] > 10)) visible++;
      }}
      return visible >= 8;
    }} catch (e) {{
      return img.complete && img.naturalWidth > 0;
    }}
  }}
  function waitThenSwap(loader, mySeq){{
    var checks = 0;
    function check(){{
      if (mySeq !== seq) return;
      checks++;
      if (hasVisiblePixels(loader)) {{
        var photo = byId('photo');
        if (photo) photo.src = loader.src;
        loading = false;
        setState(paused ? 'Paused' : 'Playing');
        scheduleNext();
        return;
      }}
      if (checks >= 80) {{
        loading = false;
        setState('Blank skipped');
        retryTimer = window.setTimeout(function(){{ requestAsset('next'); }}, 1000);
        return;
      }}
      window.setTimeout(check, 250);
    }}
    window.setTimeout(check, 120);
  }}
  function loadAsset(assetId){{
    seq++;
    var mySeq = seq;
    clearTimers();
    loading = true;
    setState('Loading…');
    var loader = new Image();
    loader.onload = function(){{ waitThenSwap(loader, mySeq); }};
    loader.onerror = function(){{
      if (mySeq !== seq) return;
      loading = false;
      setState('Load failed; retrying');
      retryTimer = window.setTimeout(function(){{ requestAsset('next'); }}, 5000);
    }};
    loader.src = '/image?id=' + encodeURIComponent(assetId) + '&ts=' + (new Date().getTime());
  }}
  function requestAsset(dir){{
    if (loading) return;
    clearTimers();
    setState(dir === 'prev' ? 'Previous…' : 'Next…');
    var xhr = new XMLHttpRequest();
    xhr.onreadystatechange = function(){{
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200) {{
        try {{
          var doc = JSON.parse(xhr.responseText);
          if (doc && doc.id) return loadAsset(doc.id);
        }} catch(e) {{}}
      }}
      setState('Source error; retrying');
      retryTimer = window.setTimeout(function(){{ requestAsset('next'); }}, 5000);
    }};
    xhr.open('GET', '/asset?dir=' + encodeURIComponent(dir || 'next') + '&ts=' + (new Date().getTime()), true);
    xhr.send(null);
  }}
  window.showControls = function(){{
    var c = byId('overlay');
    if (!c) return;
    c.className = 'active';
    if (window.overlayTimer) window.clearTimeout(window.overlayTimer);
    window.overlayTimer = window.setTimeout(function(){{ c.className = ''; }}, 3500);
  }};
  var touchStartX = 0;
  var touchStartY = 0;
  var lastControlTap = 0;
  window.controlStart = function(evt){{
    evt = evt || window.event;
    if (evt.touches && evt.touches.length) {{ touchStartX = evt.touches[0].clientX; touchStartY = evt.touches[0].clientY; }}
    else {{ touchStartX = evt.clientX || 640; touchStartY = evt.clientY || 400; }}
    return true;
  }};
  window.controlMove = function(evt){{
    evt = evt || window.event;
    var y = touchStartY || 400;
    var moveY = y;
    if (evt.touches && evt.touches.length) moveY = evt.touches[0].clientY;
    else if (typeof evt.clientY === 'number') moveY = evt.clientY;
    if ((y - moveY) > 80) {{ if (evt.preventDefault) evt.preventDefault(); window.showControls(); return false; }}
    return true;
  }};
  window.controlTap = function(evt){{
    var now = new Date().getTime();
    if (now - lastControlTap < 450) return false;
    evt = evt || window.event;
    var x = touchStartX || 640;
    var y = touchStartY || 400;
    var endX = x;
    var endY = y;
    if (evt.changedTouches && evt.changedTouches.length) {{ endX = evt.changedTouches[0].clientX; endY = evt.changedTouches[0].clientY; }}
    else if (typeof evt.clientX === 'number') {{ endX = evt.clientX; endY = evt.clientY; }}
    if (evt.preventDefault) evt.preventDefault();
    if ((y - endY) > 80) {{ window.showControls(); return false; }}
    lastControlTap = now;
    if (endX < 426) return window.framePrev();
    if (endX > 853) return window.frameNext();
    return window.framePause();
  }};
  window.framePrev = function(){{ requestAsset('prev'); return false; }};
  window.frameNext = function(){{ requestAsset('next'); return false; }};
  window.framePause = function(){{
    paused = !paused;
    setState(paused ? 'Paused' : 'Playing');
    if (paused) clearTimers(); else scheduleNext();
    window.showControls();
    return false;
  }};
  window.onload = function(){{ requestAsset('current'); }};
}})();
</script></head>
<body><div id='wrap'><img id='photo' width='1280' height='800'></div>
<div id='hit' ontouchstart='return controlStart(event)' ontouchmove='return controlMove(event)' ontouchend='return controlTap(event)' onmousedown='return controlStart(event)' onmousemove='return controlMove(event)' onclick='return controlTap(event)'></div>
<div id='overlay'><div id='state'>Starting…</div><div id='badge'>{status}</div></div></body></html>"""
                return self.send_bytes(200, body.encode(), "text/html; charset=utf-8")
            if parsed.path == "/status":
                mode, ids = refresh_assets()
                cache_count = 0
                try:
                    cache_count = len([name for name in os.listdir(FRAME_CACHE_DIR) if name.endswith(".jpg")])
                except Exception:
                    pass
                doc = {"ok": True, "immich": IMMICH_BASE, "keyNames": IMMICH_API_KEY_NAMES, "mode": mode, "count": len(ids), "slideSeconds": SLIDE_SECONDS, "cacheCount": cache_count, "prefetchCount": PREFETCH_COUNT}
                return self.send_bytes(200, json.dumps(doc).encode(), "application/json")
            if parsed.path == "/asset":
                mode, ids = refresh_assets()
                if not ids:
                    return self.send_bytes(503, b"No Immich image assets found")
                params = urllib.parse.parse_qs(parsed.query)
                direction = (params.get("dir") or ["next"])[0]
                asset_id = select_asset(direction, ids)
                if not asset_id:
                    return self.send_bytes(503, b"No Immich image assets found")
                start_prefetch()
                doc = {"ok": True, "id": asset_id, "mode": mode, "count": len(ids)}
                return self.send_bytes(200, json.dumps(doc).encode(), "application/json")
            if parsed.path == "/image":
                mode, ids = refresh_assets()
                if not ids:
                    return self.send_bytes(503, b"No Immich image assets found")
                params = urllib.parse.parse_qs(parsed.query)
                asset_id = (params.get("id") or [""])[0]
                if not asset_id:
                    asset_id = select_asset("next", ids)
                if not asset_id:
                    return self.send_bytes(503, b"No Immich image assets found")
                key_name = _asset_cache.get("asset_keys", {}).get(asset_id)
                data, ctype, cache_state = fetch_preview(asset_id, key_name)
                start_prefetch()
                return self.send_bytes(200, data, ctype, {"X-Immich-Source": mode, "X-Frame-Cache": cache_state, "X-Immich-Asset": asset_id, "X-Immich-Key-Name": key_name or ""})
            return self.send_bytes(404, b"not found")
        except Exception as e:
            msg = ("Bridge error: %s: %s" % (type(e).__name__, e)).encode()
            return self.send_bytes(500, msg)


def main():
    # Verify early; fail loudly instead of serving a blank shrine to entropy.
    version = immich_json("/api/server/version")
    mode, ids = refresh_assets(force=True)
    print(f"ImmichFrameoBridge listening on http://{HOST}:{PORT}/frameo -> {IMMICH_BASE} v{version.get('major')}.{version.get('minor')}.{version.get('patch')} source={mode} count={len(ids)}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
