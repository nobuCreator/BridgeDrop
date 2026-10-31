"""Captures BridgeDrop's screens in real browsers, so its look on a machine nobody has at hand can be checked.

In CI this runs on macOS and the images are kept as a build artifact. Needs: pip install playwright, then
playwright install chromium webkit. Usage: python tests/screenshots.py OUTPUT_FOLDER [chromium webkit ...]
"""
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridgedrop  # noqa: E402

PORT = 18791
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"


def post(url, body=b""):
    return urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"), timeout=20).read()


def seed(token, lan):
    """Sample history as a phone at the network address would produce it."""
    if not lan:
        return
    me = f"ip-{lan}"
    post(f"http://{lan}:{PORT}/api/ping?t={token}")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    for name, mime, body in (("夏休みの写真.png", "image/png", png), ("見積書.pdf", "application/pdf", b"%PDF"), ("旅行の動画.mp4", "video/mp4", b"m")):
        query = urllib.parse.urlencode({"t": token, "to": me, "name": name, "type": mime})
        post(f"http://127.0.0.1:{PORT}/api/upload?{query}", body)
    post(f"http://{lan}:{PORT}/api/text?t={token}&to=pc", "https://example.com メモです".encode())


def capture(browser, name, url, out, *, scheme="light", viewport=(620, 860), user_agent=None, prepare=None):
    context = browser.new_context(color_scheme=scheme, viewport={"width": viewport[0], "height": viewport[1]},
                                  device_scale_factor=2, user_agent=user_agent)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_timeout(1200)
    if prepare:
        prepare(page)
        page.wait_for_timeout(800)
    page.screenshot(path=str(out / f"{name}.png"))
    context.close()
    return errors


def main():
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    engines = sys.argv[2:] or ["chromium", "webkit"]
    problems = {}
    with tempfile.TemporaryDirectory() as temp:
        temp = pathlib.Path(temp)
        server = subprocess.Popen([sys.executable, str(ROOT / "bridgedrop.py"), "--no-browser", "--port", str(PORT),
                                   "--data-dir", str(temp / "data"), "--save-dir", str(temp / "save")])
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=1).read()
                    break
                except OSError:
                    time.sleep(0.2)
            token = (temp / "data" / "token.txt").read_text().strip()
            addresses = bridgedrop.local_addresses()
            lan = addresses[0] if addresses else None
            home = f"http://127.0.0.1:{PORT}/?t={token}"
            with sync_playwright() as playwright:
                for engine in engines:
                    browser = getattr(playwright, engine).launch()
                    shot = lambda name, url, **options: problems.update(
                        {f"{engine}-{name}": capture(browser, f"{engine}-{name}", url, out, **options)})
                    shot("pc-not-connected", home)
                    shot("pc-dark", home, scheme="dark")
                    def show_lan_code(page):
                        page.click("#tab-lan")
                        if lan:
                            page.wait_for_selector("#qr svg", timeout=8000)

                    shot("pc-same-network", home, prepare=show_lan_code)
                    shot("pc-quit-dialog", home, prepare=lambda page: page.click("#quit"))
                    if lan:
                        phone = f"http://{lan}:{PORT}/?t={token}"
                        seed(token, lan)
                        shot("android", phone, viewport=(412, 915), user_agent=ANDROID)
                        shot("android-dark", phone, scheme="dark", viewport=(412, 915), user_agent=ANDROID)
                        shot("android-select", phone, viewport=(412, 915), user_agent=ANDROID,
                             prepare=lambda page: (page.click("#select-toggle"), page.click("#items .row:nth-child(1)"), page.click("#items .row:nth-child(2)")))
                    browser.close()
        finally:
            server.terminate()
            server.wait(10)
    found = {name: errors for name, errors in problems.items() if errors}
    (out / "page-errors.json").write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(problems)} screens captured, {len(found)} with page errors")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
