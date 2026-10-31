"""Checks of the system-specific branches, including the macOS ones, on any machine."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("winreg", None) if sys.platform != "win32" else None
import bridgedrop  # noqa: E402


class MacBranches(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(bridgedrop, IS_MAC=True, IS_WINDOWS=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_opens_chrome_app_window(self):
        with mock.patch.object(bridgedrop, "find_mac_app_browser", return_value="Google Chrome"), \
                mock.patch.object(bridgedrop.subprocess, "Popen") as popen, \
                mock.patch.object(bridgedrop.webbrowser, "open") as browser:
            bridgedrop.open_app_window("http://127.0.0.1:1/")
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["open", "-na", "Google Chrome"])
        self.assertIn("--app=http://127.0.0.1:1/", command)
        browser.assert_not_called()

    def test_falls_back_to_default_browser(self):
        with mock.patch.object(bridgedrop, "find_mac_app_browser", return_value=None), \
                mock.patch.object(bridgedrop.subprocess, "Popen") as popen, \
                mock.patch.object(bridgedrop.webbrowser, "open") as browser:
            bridgedrop.open_app_window("http://127.0.0.1:1/")
        popen.assert_not_called()
        browser.assert_called_once()

    def test_reveals_in_finder(self):
        with mock.patch.object(bridgedrop.subprocess, "Popen") as popen:
            bridgedrop.reveal_in_file_manager(pathlib.Path("/tmp/a.png"), pathlib.Path("/tmp"))
            self.assertEqual(popen.call_args.args[0][:2], ["open", "-R"])
            bridgedrop.reveal_in_file_manager(None, pathlib.Path("/tmp"))
            self.assertEqual(popen.call_args.args[0][0], "open")
            self.assertNotIn("-R", popen.call_args.args[0])

    def test_direct_link_reports_unsupported(self):
        store = mock.Mock()
        link = bridgedrop.DirectLink(pathlib.Path("."), store, lambda ip: ip)
        self.assertEqual(link.public()["status"], "unsupported")
        link.stop()
        self.assertEqual(link.public()["status"], "unsupported")

    def test_fatal_error_needs_no_windows_api(self):
        with mock.patch.object(bridgedrop.sys, "stderr"):
            bridgedrop.show_fatal_error("x")

    def test_never_resolves_own_host_name(self):
        """It can block for many seconds on macOS, which made the pairing code time out."""
        run = mock.Mock(stdout="en0: flags\n\tinet 192.168.1.5 netmask 0xffffff00\n")
        with mock.patch.object(bridgedrop.subprocess, "run", return_value=run), \
                mock.patch.object(bridgedrop.socket, "gethostbyname_ex") as resolve:
            addresses = bridgedrop.local_addresses()
        resolve.assert_not_called()
        self.assertIn("192.168.1.5", addresses)

    def test_finds_addresses_from_ifconfig_when_host_name_fails(self):
        output = "lo0: flags\n\tinet 127.0.0.1 netmask 0xff000000\nen0: flags\n\tinet 172.20.10.2 netmask 0xfffffff0 broadcast 172.20.10.15\n\tinet6 fe80::1 prefixlen 64\n"
        self.assertEqual(bridgedrop.parse_ifconfig(output), ["127.0.0.1", "172.20.10.2"])
        run = mock.Mock(stdout=output)
        with mock.patch.object(bridgedrop.subprocess, "run", return_value=run), \
                mock.patch.object(bridgedrop.socket, "gethostbyname_ex", side_effect=OSError), \
                mock.patch.object(bridgedrop.socket, "socket", side_effect=OSError):
            self.assertEqual(bridgedrop.local_addresses(), ["172.20.10.2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
