import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from kosika.controls import ExecutionError
from kosika.model import ModelRequest
from kosika.workbench import dispatch
from kosika.workbench_samples import SAMPLES
from kosika.workbench_support import LiveConfig, diagnostic


def model_payload():
    return {
        key: copy.deepcopy(SAMPLES[1][key])
        for key in ("files", "entry", "workflow", "inputs", "fixtures")
    }


class WorkbenchV2Tests(unittest.TestCase):
    def test_checked_record_schema_and_exact_parser_location(self):
        data = model_payload()
        result = dispatch(
            "check", {key: data[key] for key in ("files", "entry", "workflow")}
        )
        schema = result["input_schema"]
        self.assertEqual(
            schema["properties"]["question"]["properties"]["text"], {"type": "string"}
        )
        self.assertFalse(schema["additionalProperties"])
        data["files"]["main.kos"] = "record R { text Text }"
        with self.assertRaises(ValueError) as caught:
            dispatch("run", data)
        info = diagnostic(str(caught.exception))
        self.assertEqual(info["file"], "main.kos")
        self.assertEqual(info["line"], 1)
        self.assertGreater(info["column"], 1)
        self.assertNotIn("file", diagnostic("An invalid record has no source location"))

    def test_live_configuration_and_browser_fail_closed(self):
        for url in (
            "https://example.org/v1",
            "http://localhost:12/v1",
            "http://127.0.0.1:12/v1?x=1",
            "http://a:b@127.0.0.1:12/v1",
            "http://127.0.0.1/v1",
            "http://127.0.0.1:12/admin",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LiveConfig(url, "local")
        data = model_payload()
        data["mode"] = "live"
        with self.assertRaisesRegex(ValueError, "not configured"):
            dispatch("run", data)
        data["mode"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "Unknown execution mode"):
            dispatch("run", data)
        data["mode"] = "live"
        data["model_url"] = "http://127.0.0.1:1/v1"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            dispatch("run", data, live=LiveConfig("http://127.0.0.1:12/v1", "local"))

    def test_live_http_validation_telemetry_replay_and_redirect_denial(self):
        calls = []
        response_mode = ["valid"]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                calls.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                if response_mode[0] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.org/forbidden")
                    self.end_headers()
                    return
                content = (
                    {"text": "A local summary."}
                    if response_mode[0] == "valid"
                    else {"text": 42}
                )
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"content": json.dumps(content)},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 6,
                            "total_tokens": 18,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = LiveConfig(
                    f"http://127.0.0.1:{server.server_port}/v1", "test-local"
                )
                result = dispatch(
                    "run", {**model_payload(), "mode": "live"}, live=config
                )
                self.assertEqual(result["mode"], "live")
                self.assertEqual(calls[0]["model"], "test-local")
                self.assertEqual(calls[0]["max_tokens"], 1024)
                self.assertEqual(
                    result["telemetry"]["writer"][0]["usage"]["total_tokens"], 18
                )
                self.assertEqual(
                    dispatch("replay", {"trace": result["trace"]})["result"],
                    result["result"],
                )
                self.assertEqual(len(calls), 1)
                response_mode[0] = "invalid"
                with self.assertRaisesRegex(ValueError, "string"):
                    dispatch("run", {**model_payload(), "mode": "live"}, live=config)
                response_mode[0] = "redirect"
                with self.assertRaisesRegex(ExecutionError, "redirects"):
                    config.provider().complete(ModelRequest(messages=()))
            finally:
                server.shutdown()
                thread.join(3)

    def test_timeout_is_reported_without_transport_details(self):
        config = LiveConfig("http://127.0.0.1:12/v1", "test-local")
        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=TimeoutError("private detail"),
        ):
            with self.assertRaisesRegex(ExecutionError, "timed out") as caught:
                config.provider().complete(ModelRequest(messages=()))
        self.assertNotIn("private detail", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
