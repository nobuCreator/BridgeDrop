"""Captures the screens used in the user manual from the running app, with placeholder names and addresses.

Needs: pip install playwright pillow, then playwright install chromium. Writes docs/images/.
"""
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridgedrop  # noqa: E402
import qr  # noqa: E402

PORT = 18792
OUT = ROOT / "docs" / "images"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.7 Mobile/15E148 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
PC_NAME = "マイPC"
PLACEHOLDER_IP = "192.168.1.10"
# The real codes hold this machine's address and pairing token, so the images must only ever show these.
SAMPLE_PAGE_QR = qr.to_svg(f"http://{PLACEHOLDER_IP}:8765/?t=sample")
SAMPLE_WIFI_QR = qr.to_svg("WIFI:T:WPA;S:sample;P:sample;;")
fake_direct = {}


def post(url, body=b""):
    return urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"), timeout=20).read()


def sample_photo():
    image = Image.new("RGB", (900, 600))
    pixels = image.load()
    for y in range(600):
        for x in range(900):
            t = y / 600
            pixels[x, y] = (int(120 + 90 * t), int(180 - 40 * t + x / 30), int(235 - 90 * t))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
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
            lan = bridgedrop.local_addresses()[0]
            home = f"http://127.0.0.1:{PORT}/?t={token}"
            phone_url = f"http://{lan}:{PORT}/?t={token}"
            with sync_playwright() as playwright:
                capture(playwright.chromium.launch(), token, lan, home, phone_url)
        finally:
            server.terminate()
            server.wait(10)
    print(sorted(path.name for path in OUT.glob("*.png")))


def use_placeholders(context):
    """Replaces this machine's name, address and pairing token before the page ever sees them."""

    def handle_state(route):
        response = route.fetch()
        data = response.json()
        names = {}
        for device in data["devices"]:
            device["name"] = PC_NAME if device["id"] == "pc" else {"iphone": "iPhone", "android": "Android"}.get(device["kind"], "スマホ")
            names[device["id"]] = device["name"]
        for item in data["items"]:
            item["fromName"] = names.get(item["from"], "スマホ")
            item["toName"] = names.get(item["to"], "スマホ")
        if "direct" in data and fake_direct:
            data["direct"] = fake_direct
        route.fulfill(response=response, body=json.dumps(data, ensure_ascii=False))

    def handle_pairing(route):
        address = {"ip": PLACEHOLDER_IP, "url": f"http://{PLACEHOLDER_IP}:8765/?t=sample", "qr": SAMPLE_PAGE_QR}
        route.fulfill(json={"addresses": [address]})

    context.route("**/api/state*", handle_state)
    context.route("**/api/pairing*", handle_pairing)


def new_page(browser, width, height, user_agent=None):
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                  user_agent=user_agent, color_scheme="light")
    use_placeholders(context)
    page = context.new_page()
    return context, page


def shot(page, name, wait=900):
    page.wait_for_timeout(wait)
    page.screenshot(path=str(OUT / f"{name}.png"))


def show_direct(page, clients):
    fake_direct.update(status="on", mode="hotspot", ssid="マイPCのWi-Fi", passphrase="パスワードの例", ip="sample", url="sample",
                       wifiQr=SAMPLE_WIFI_QR, urlQr=SAMPLE_PAGE_QR, portal=True, clients=clients)
    page.evaluate("() => refresh()")


def capture(browser, token, lan, home, phone_url):
    # Computer, nobody connected yet.
    pc_context, pc = new_page(browser, 620, 760)
    pc.goto(home)
    pc.wait_for_timeout(900)
    pc.click("#tab-lan")
    pc.wait_for_selector("#qr svg")
    shot(pc, "pc-same-network")

    # Direct connection, from the screens the app shows once the hotspot is on.
    pc.click("#tab-direct")
    shot(pc, "pc-direct-start")
    show_direct(pc, 0)
    shot(pc, "pc-direct-wifi-qr")
    show_direct(pc, 1)
    shot(pc, "pc-direct-page-qr")
    fake_direct.clear()
    pc.evaluate("() => refresh()")

    # A phone joins, and each side has received something.
    iphone_context, iphone = new_page(browser, 390, 844, IPHONE)
    iphone.goto(phone_url)
    iphone.wait_for_timeout(1000)
    me = f"ip-{lan}"
    shot(iphone, "phone-iphone-start")

    pc.reload()
    pc.wait_for_timeout(900)
    pc.evaluate("() => { document.getElementById('pair').open = false; renderPairing.touched = true; }")
    shot(pc, "pc-ready")

    for name, mime, body in (("旅行の写真.jpg", "image/jpeg", sample_photo()), ("見積書.pdf", "application/pdf", b"%PDF-1.4"),
                             ("発表資料.pptx", "application/octet-stream", b"pptx")):
        query = urllib.parse.urlencode({"t": token, "to": me, "name": name, "type": mime})
        post(f"http://127.0.0.1:{PORT}/api/upload?{query}", body)
    query = urllib.parse.urlencode({"t": token, "to": "pc", "name": "IMG_0123.jpg", "type": "image/jpeg"})
    post(f"http://{lan}:{PORT}/api/upload?{query}", sample_photo())
    # Long enough for the arrival notice to fade, since it would cover part of the list.
    iphone.wait_for_timeout(3600)
    iphone.evaluate("() => refresh()")
    shot(iphone, "phone-received")

    # Selecting several.
    iphone.click("#select-toggle")
    rows = iphone.locator("#items .row")
    rows.nth(0).click()
    rows.nth(1).click()
    shot(iphone, "phone-select")
    iphone.click("#select-toggle")

    # A received photo, on its own page.
    photo = iphone.locator("#items .row", has_text="旅行の写真").locator("a", has_text="写真に保存")
    photo.click()
    shot(iphone, "phone-photo-page")
    iphone_context.close()

    # Android's two buttons.
    android_context, android = new_page(browser, 412, 760, ANDROID)
    android.goto(phone_url)
    shot(android, "phone-android-start", 1000)
    android_context.close()

    # Quitting.
    pc.reload()
    pc.wait_for_timeout(800)
    pc.evaluate("() => { document.getElementById('pair').open = false; renderPairing.touched = true; }")
    pc.click("#quit")
    shot(pc, "pc-quit")
    pc_context.close()

    # A guide to the download page, drawn rather than captured because it must not show a real account's details.
    guide_context, guide = new_page(browser, 720, 300)
    guide.set_content("""<!doctype html><meta charset=utf-8><style>
      body { margin: 0; padding: 20px; font: 15px/1.5 -apple-system, "Yu Gothic UI", Meiryo, sans-serif; color: #1f2328; background: #fff; }
      .box { border: 1px solid #d0d7de; border-radius: 8px; }
      .head { padding: 10px 14px; background: #f6f8fa; border-bottom: 1px solid #d0d7de; font-weight: 600; border-radius: 8px 8px 0 0; }
      .row { display: flex; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid #d8dee4; color: #0969da; }
      .row:last-child { border-bottom: 0; color: #57606a; }
      .row.pick { background: #fff8c5; font-weight: 700; }
      .row span:last-child { color: #57606a; font-weight: 400; }
      .note { margin-top: 10px; font-size: 13px; color: #57606a; }
    </style><div class=box><div class=head>Assets（ダウンロードできるファイル）</div>
    <div class="row pick"><span>BridgeDrop-1.0.0-windows.zip</span><span>← Windowsの方はこれ</span></div>
    <div class="row pick"><span>BridgeDrop-1.0.0-mac.zip</span><span>← Macの方はこれ</span></div>
    <div class=row><span>Source code (zip)</span><span>使いません</span></div>
    <div class=row><span>Source code (tar.gz)</span><span>使いません</span></div></div>
    <div class=note>説明用のイメージ図です。数字の部分は、そのときの最新の番号に変わります。</div>""")
    shot(guide, "download-guide", 200)
    guide_context.close()
    browser.close()


if __name__ == "__main__":
    main()
