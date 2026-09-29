import http.client
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from kosika.workbench_samples import SAMPLES


class PackagedHostTests(unittest.TestCase):
    def test_assets_execution_and_parent_pipe_shutdown(self):
        binary = os.environ.get("KOSIKA_TEST_HOST")
        command = (
            [str(Path(binary).resolve())]
            if binary
            else [sys.executable, "-B", "-m", "kosika.packaged_host"]
        )
        # Frozen builds must work from an unrelated directory, without source imports.
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [*command, "--port", "0"],
                cwd=directory if binary else None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                ready = queue.Queue()
                threading.Thread(
                    target=lambda: ready.put(process.stdout.readline()), daemon=True
                ).start()
                state = json.loads(ready.get(timeout=30))
                self.assertTrue(state["ready"])

                def request(method, path, body=None, token=None):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", state["port"], timeout=10
                    )
                    try:
                        headers = {"Content-Type": "application/json"}
                        if token:
                            headers["X-Kosika-Token"] = token
                        connection.request(method, path, body, headers)
                        response = connection.getresponse()
                        return response.status, response.read()
                    finally:
                        connection.close()

                status, html = request("GET", "/")
                self.assertEqual(status, 200)
                self.assertIn(b"Kosika", html)
                token = json.loads(request("GET", "/api/session")[1])["token"]
                sample = SAMPLES[1]
                payload = json.dumps({
                    key: sample[key]
                    for key in ("files", "entry", "workflow", "inputs", "fixtures")
                })
                self.assertEqual(request("POST", "/api/run", payload)[0], 403)
                status, body = request("POST", "/api/run", payload, token)
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["mode"], "fixture")
                process.stdin.close()
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr.read())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
