"""End-to-end checks of the server on whichever system runs them: Windows locally, macOS in CI."""
import http.client
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridgedrop  # noqa: E402

PORT = 18790


def free_address():
    addresses = bridgedrop.local_addresses()
    return addresses[0] if addresses else None


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.temp.name)
        cls.save_dir = base / "save"
        # Set BRIDGEDROP_APP to run these same checks against a packaged build instead of the source.
        packaged = os.environ.get("BRIDGEDROP_APP")
        command = [packaged] if packaged else [sys.executable, str(ROOT / "bridgedrop.py")]
        cls.proc = subprocess.Popen([*command, "--no-browser", "--port", str(PORT),
                                     "--data-dir", str(base / "data"), "--save-dir", str(cls.save_dir)])
        started = time.monotonic()
        cls.startup_seconds = None
        while time.monotonic() - started < 60:
            try:
                # The answer, not just the socket: a bound port can accept connections before the server serves them.
                probe = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
                probe.request("GET", "/healthz")
                probe.getresponse().read()
                probe.close()
                cls.startup_seconds = time.monotonic() - started
                break
            except OSError:
                time.sleep(0.1)
        cls.token = (base / "data" / "token.txt").read_text().strip()
        cls.lan = free_address()

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        cls.temp.cleanup()

    def request(self, method, path, body=None, host="127.0.0.1", authorize=True, headers=None):
        connection = http.client.HTTPConnection(host, PORT, timeout=20)
        separator = "&" if "?" in path else "?"
        target = f"{path}{separator}t={self.token}" if authorize else path
        connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def state(self, host="127.0.0.1"):
        return json.loads(self.request("GET", "/api/state", host=host)[1])

    def upload(self, name, body, to, host="127.0.0.1"):
        query = urllib.parse.urlencode({"to": to, "name": name})
        status, data = self.request("POST", f"/api/upload?{query}", body, host=host)
        self.assertEqual(status, 200, data)
        return json.loads(data)["id"]

    def test_starts_quickly(self):
        """Looking up the machine's own name once kept a Mac unreachable for over a minute at startup."""
        self.assertIsNotNone(self.startup_seconds)
        self.assertLess(self.startup_seconds, 10)

    def test_pairing_answers_quickly(self):
        started = time.monotonic()
        self.request("GET", "/api/pairing")
        self.assertLess(time.monotonic() - started, 5)

    def test_reports_version(self):
        self.assertEqual(self.state()["version"], bridgedrop.__version__)
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('id="version"', page.decode())

    def test_page_and_auth(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/", authorize=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/state", authorize=False)[0], 401)

    def test_pc_kind_and_direct_status(self):
        state = self.state()
        pc = next(device for device in state["devices"] if device["id"] == "pc")
        self.assertEqual(pc["kind"], "windows" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "other")
        self.assertEqual(state["direct"]["status"], "off" if sys.platform == "win32" else "unsupported")

    @unittest.skipIf(sys.platform == "win32", "Windows can start the link")
    def test_direct_refused_without_windows(self):
        self.assertEqual(self.request("POST", "/api/direct?action=start&mode=hotspot")[0], 501)

    def test_pairing_offers_a_scannable_address(self):
        pairing = json.loads(self.request("GET", "/api/pairing")[1])
        if self.lan:
            self.assertTrue(pairing["addresses"])
            self.assertIn("<svg", pairing["addresses"][0]["qr"])

    def test_transfer_both_ways(self):
        if not self.lan:
            self.skipTest("no network address on this machine")
        self.request("POST", "/api/ping", host=self.lan)
        me = f"ip-{self.lan}"
        self.assertIn(me, [device["id"] for device in self.state()["devices"]])

        body = bytes(range(256)) * 2000
        sent = self.upload("電話から.bin", body, "pc", host=self.lan)
        saved = [item for item in self.state()["items"] if item["id"] == sent][0]
        self.assertTrue(saved["saved"])
        self.assertEqual((self.save_dir / saved["name"]).read_bytes(), body)

        first = self.upload("写真.png", b"png-data", me)
        second = self.upload("写真.png", b"other-data", me)
        status, data = self.request("GET", f"/file/{first}", host=self.lan)
        self.assertEqual((status, data), (200, b"png-data"))

        status, data = self.request("GET", f"/zip?ids={first},{second}", host=self.lan)
        self.assertEqual(status, 200)
        bundle = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(sorted(bundle.namelist()), sorted(["写真.png", "写真 (1).png"]))
        self.assertIsNone(bundle.testzip())

    def test_android_gets_android_hints(self):
        if not self.lan:
            self.skipTest("no network address on this machine")
        me = f"ip-{self.lan}"
        self.request("POST", "/api/ping", host=self.lan)
        item = self.upload("画像.png", b"x", me)
        android = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile Safari/537.36"}
        page = self.request("GET", f"/view/{item}", host=self.lan, headers=android)[1].decode()
        self.assertIn("画像をダウンロード", page)
        self.assertNotIn("写真アプリ", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
