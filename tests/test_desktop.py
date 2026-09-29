import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from kosika.desktop import active_session, launch, open_window, request, stop


@unittest.skipUnless(os.name == "nt", "Windows desktop launcher")
class DesktopTests(unittest.TestCase):
    def test_single_instance_authenticated_shutdown_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for _ in range(2):
                errors = []

                def serve():
                    try:
                        launch(root, show_window=False, port=0)
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                try:
                    state = None
                    for _ in range(50):
                        state = active_session(root / "session.json")
                        if state:
                            break
                        time.sleep(0.05)
                    self.assertIsNotNone(state)
                    assert state is not None
                    with patch("kosika.desktop.open_window") as window:
                        launch(root, port=0)
                        window.assert_called_once_with(state["port"], root)
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        request(state["port"], "/api/desktop/stop", "wrong-token")
                    self.assertEqual(rejected.exception.code, 403)
                    self.assertIsNotNone(active_session(root / "session.json"))
                    self.assertTrue(stop(root))
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse((root / "session.json").exists())
                    self.assertEqual(errors, [])
                finally:
                    stop(root)
                    thread.join(timeout=5)

    def test_stale_or_foreign_session_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "session.json"
            state_path.write_text(
                json.dumps({"port": 12345, "token": "expected"}), encoding="utf-8"
            )
            with patch("kosika.desktop.request", return_value={"token": "different"}):
                self.assertIsNone(active_session(state_path))
            state_path.write_text("not json", encoding="utf-8")
            self.assertIsNone(active_session(state_path))

    def test_browser_uses_app_window_and_separate_profile(self) -> None:
        with (
            patch("kosika.desktop.browser_path", return_value=Path("edge.exe")),
            patch("kosika.desktop.subprocess.Popen") as spawn,
        ):
            open_window(8792, Path("C:/Kosika Data"))
        arguments = spawn.call_args.args[0]
        self.assertIn("--app=http://127.0.0.1:8792/", arguments)
        self.assertTrue(
            any(
                arg.startswith("--user-data-dir=") and "Kosika Data" in arg
                for arg in arguments
            )
        )
        with self.assertRaises(ValueError):
            open_window(0, Path("unused"))
