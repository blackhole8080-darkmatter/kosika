import copy
import http.client
import json
import threading
import unittest

from kosika.workbench import MAX_BODY, WorkbenchServer, dispatch
from kosika.workbench_samples import SAMPLES


def payload(sample):
    return {
        key: copy.deepcopy(sample[key])
        for key in ("files", "entry", "workflow", "inputs", "fixtures")
    }


class WorkbenchTests(unittest.TestCase):
    def test_examples_run_and_replay_with_real_runtime(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample["id"]):
                data = payload(sample)
                checked = dispatch(
                    "check", {key: data[key] for key in ("files", "entry", "workflow")}
                )
                self.assertIn("main", checked["summary"]["workflows"])
                result = dispatch("run", data)
                self.assertEqual(
                    dispatch("replay", {"trace": result["trace"]})["result"],
                    result["result"],
                )
                self.assertEqual(result["mode"], "fixture")
                self.assertGreater(len(result["events"]), 0)

    def test_invalid_types_and_source_produce_diagnostics(self):
        data = payload(SAMPLES[1])
        data["fixtures"]["writer"] = [{"text": 7}]
        with self.assertRaisesRegex(ValueError, "string"):
            dispatch("run", data)
        data["files"]["main.kos"] = "workflow broken("
        with self.assertRaises(ValueError) as caught:
            dispatch("run", data)
        self.assertIn("main.kos", str(caught.exception))
        self.assertNotIn("kosika-workbench-", str(caught.exception))
        with self.assertRaises(ValueError):
            dispatch("replay", {"trace": "{}"})

    def test_paths_and_external_tool_grants_fail_closed(self):
        for name in (
            "../outside.kos",
            "/absolute.kos",
            "C:/outside.kos",
            "NUL.kos",
            "sub//a.kos",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dispatch(
                    "run", {"files": {name: "record R { text: Text }"}, "entry": name}
                )
        data = payload(SAMPLES[2])
        data["files"]["main.kos"] = data["files"]["main.kos"].replace(
            "local_mock", "external"
        )
        with self.assertRaisesRegex(ValueError, "read-only local_mock"):
            dispatch("run", data)
        with self.assertRaises(ValueError):
            dispatch("run", {**payload(SAMPLES[1]), "model_url": "http://external"})
        data = payload(SAMPLES[1])
        data["files"]["main.kos"] = (
            'import "../external.kos"\n' + data["files"]["main.kos"]
        )
        with self.assertRaises(ValueError):
            dispatch("run", data)

    def test_fixture_tool_calls_and_format(self):
        data = payload(SAMPLES[3])
        data["fixtures"]["reader"] = [
            {"$tool_calls": [{"id": "1", "name": "shell", "arguments": {}}]}
        ]
        with self.assertRaisesRegex(ValueError, "undeclared"):
            dispatch("run", data)
        data["fixtures"]["reader"] = [{"$tool_calls": [{}]}]
        with self.assertRaises(ValueError):
            dispatch("run", data)
        self.assertIn(
            "record",
            dispatch("format", {"source": "record R { text: Text }"})["source"],
        )

    def test_workbench_server_origin_token_and_size_boundaries(self):
        server = WorkbenchServer(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()

        try:
            status, body, headers = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b"Kosika", body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(request("GET", "/../README.md")[0], 404)
            self.assertEqual(
                request("GET", "/api/session", headers={"Host": "evil.example"})[0], 403
            )
            status, body, _ = request("GET", "/api/session")
            token = json.loads(body)["token"]
            data = json.dumps(payload(SAMPLES[1]))
            self.assertEqual(
                request("POST", "/api/run", data, {"Content-Type": "application/json"})[
                    0
                ],
                403,
            )
            valid = {"Content-Type": "application/json", "X-Kosika-Token": token}
            self.assertEqual(
                request(
                    "POST",
                    "/api/run",
                    data,
                    {**valid, "Origin": "https://evil.example"},
                )[0],
                403,
            )
            self.assertEqual(
                request(
                    "POST", "/api/run", data, {**valid, "Content-Type": "text/plain"}
                )[0],
                415,
            )
            self.assertEqual(
                request(
                    "POST",
                    "/api/run",
                    "",
                    {**valid, "Content-Length": str(MAX_BODY + 1)},
                )[0],
                413,
            )
            self.assertEqual(
                request("POST", "/api/run", '{"a":1,"a":2}', valid)[0], 400
            )
            status, body, _ = request("POST", "/api/run", data, valid)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["ok"])
            run_events = json.loads(body)["events"]
            status, body, _ = request(
                "POST", "/api/timeline", json.dumps({"events": run_events}), valid
            )
            self.assertEqual(status, 200)
            timeline = json.loads(body)
            self.assertEqual(timeline["design"]["kind"], "event_timeline")
            self.assertIn("<svg", timeline["svg"])
            self.assertEqual(
                request("POST", "/api/timeline", '{"events": []}', valid)[0],
                400,
            )
            for asset in ("/app.js", "/styles.css", "/favicon.svg"):
                self.assertEqual(request("GET", asset)[0], 200)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
