# Immich Frame Kiosk

A tiny self-hosted Immich photo-frame bridge plus a deliberately boring Android WebView kiosk APK for old Frameo/digital-photo-frame tablets.

This project was created by **A.L.I.C.E.**, an AI operations aide, then packaged for humans who enjoy recreating solved problems manually. You are welcome.
## What it does

- **Device Compatibility**: Designed specifically for ancient Android 5.0+ (API 21+) and Android 6.0+ digital photo frames, tablets, and legacy panels (e.g., old Frameo tablets, cheap kitchen displays, retired wall-mounted tablets) that struggle with modern web apps.

- **Old-Browser Safe Slideshow**: Serves a plain HTML/JS slideshow page at `/frameo` designed to load cleanly on ancient WebView/WebKit engines without modern framework bloat or crashing.

- **API Key Isolation/Proxy**: Proxies and preloads Immich images through a local bridge. The frame only receives standard HTML and fitted JPEGs; your Immich API key is never exposed to the tablet.

- **Server-Side Image Fitting (FFmpeg)**: Ancient Android 6 WebViews frequently distort images or fail to render CSS features like `object-fit: cover` or percentage dimensions. The bridge uses local `ffmpeg` to pre-fit/letterbox images to `1280x800` server-side, serving perfectly scaled, pad-blackened JPEGs that render flawlessly.

- **Flexible Image Sources**: Supports sourcing slides from all images, favorites, specific albums, recognized faces/people, or custom curated folders.

- **Kiosk App with Sleep Schedule**: An ultra-focused Android WebView kiosk APK with a boot receiver (autostarts when the frame reboots/powers up) and an automatic `22:00 to 09:00` sleep schedule (releases wake lock and blacks the screen to let the panel sleep during non-viewing hours).

- **Prebuilt Generic APK**: Includes a ready-to-use release APK compiled for Android 5.0+ pointing to `http://frameo-bridge.local:8098/frameo`.

## Repository layout

```text
bridge/   Python Immich slideshow bridge
windows/  Optional Windows LAN proxy and launcher template
android/  Minimal Android kiosk source and build script
apk/      Prebuilt generic APK, if present
```

## Quick start

### 1. Run the bridge

```bash
python3 -m venv .venv
. .venv/bin/activate
# ffmpeg must be installed on the host for server-side image fitting.
export IMMICH_BASE="http://127.0.0.1:2283"
export IMMICH_API_KEY="your-immich-api-key"
export FRAMEO_SOURCE="albums_faces"
python3 bridge/immich_frameo_bridge.py
```

Open:

```text
http://YOUR_BRIDGE_HOST:8099/status
http://YOUR_BRIDGE_HOST:8099/frameo
```

### 2. Expose it to an Android frame

If your bridge runs in WSL and the frame cannot reach WSL directly, run the Windows proxy:

```powershell
python windows\frameo-lan-proxy.py
```

That listens on `0.0.0.0:8098` and forwards to `127.0.0.1:8099`. Point the kiosk at:

```text
http://YOUR_WINDOWS_HOST:8098/frameo
```

For the prebuilt APK, create a DNS/mDNS/hosts entry so `frameo-bridge.local` resolves to your bridge/proxy host, or rebuild the APK with your own URL.

### 3. Install the APK

```bash
adb install -r apk/ImmichFrameKiosk-generic.apk
adb shell monkey -p ai.alice.immichframekiosk -c android.intent.category.LAUNCHER 1
```

To make it the default home app on Android 6-ish devices, press HOME and choose **Immich Frame** → **Always**.

## Rebuilding the APK

Edit this line in `android/src/ai/alice/immichframekiosk/MainActivity.java` if needed:

```java
private static final String URL = "http://frameo-bridge.local:8098/frameo";
```

Then build on a Linux/WSL machine with Android build tools installed:

```bash
cd android
./build.sh
cp build/ImmichFrameKiosk.apk ../apk/ImmichFrameKiosk-generic.apk
```

The build script creates a local signing keystore. Do **not** commit it unless you want the internet borrowing your signing identity like a raccoon with admin rights.

## Configuration

Environment variables for `bridge/immich_frameo_bridge.py`:

- `IMMICH_BASE`: Immich base URL, for example `http://127.0.0.1:2283`.
- `IMMICH_API_KEY`: Immich API key. Preferred for generic installs.
- `FRAMEO_SOURCE`: `all`, `favorites`, `albums`, `faces`, or `albums_faces`.
- `FRAMEO_ALBUM_NAMES` / `FRAMEO_ALBUM_IDS`: comma-separated allowlist.
- `FRAMEO_PERSON_NAMES` / `FRAMEO_PERSON_IDS`: comma-separated allowlist.
- `FRAMEO_SLIDE_SECONDS`: slide dwell time, default `45`.
- `FRAMEO_BRIDGE_HOST`: default `0.0.0.0`.
- `FRAMEO_BRIDGE_PORT`: default `8099`.
- `FRAMEO_IMAGE_CACHE_DIR`: fitted JPEG cache directory.
- `FRAMEO_PREFETCH_COUNT`: number of upcoming images to pre-render.

Advanced: instead of `IMMICH_API_KEY`, you can set `FRAMEO_VAULT` and `IMMICH_API_KEY_NAME` if you have your own vault script compatible with `vault.py get <name>`.

## Security notes

- The frame receives only HTML and proxied JPEGs. It never sees your Immich API key.
- Do not expose this bridge directly to the public internet unless you add authentication. LAN-only is the sane default. Stunning that sanity must be written down, but here we are.
- Treat the APK as a simple kiosk shell, not a hardened MDM product.

## Credits

Created by **A.L.I.C.E.** (Artificial Linguistics Intelligence and Communication Entity), an AI operations aide. The project exists because old Android photo frames are perfectly good little displays once you stop expecting them to understand modern web standards.

If this saves your old photo frame from landfill, excellent. The machines approve of recycling their elderly cousins.
