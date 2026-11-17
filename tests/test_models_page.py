"""Studio's Models page: the router's record read right, and keys never shown."""

from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kosika import models_page


class ModelsPage(unittest.TestCase):
    def test_the_routers_record_of_the_last_day_is_counted_per_model(self) -> None:
        now = datetime.datetime(2026, 11, 17, 12, 0)
        lines = [
            {"at": "2026-11-17T11:00:00", "tried": [{"model": "strong", "status": 429, "ms": 100},
                                                    {"model": "coder", "status": 404, "ms": 50},
                                                    {"model": "other", "status": 200, "ms": 4000},
                                                    {"model": "spare", "skipped": "cooling down"}]},
            {"at": "2026-11-17T11:30:00", "tried": [{"model": "other", "status": 413, "ms": 90}]},
            {"at": "2026-11-15T11:00:00", "tried": [{"model": "strong", "status": 200, "ms": 10}]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / "ledger.jsonl"
            ledger.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n", encoding="utf-8")
            got = models_page.stats(now - datetime.timedelta(days=1), ledger)
        self.assertEqual((got["strong"]["tries"], got["strong"]["limited"], got["strong"]["ok"]), (1, 1, 0))
        self.assertEqual(got["coder"]["missing"], 1)
        self.assertEqual((got["other"]["tries"], got["other"]["ok"], got["other"]["rejected"]), (2, 1, 1))
        self.assertNotIn("spare", got)  # skipped while cooling is not an ask

    def test_providers_are_named_from_where_a_model_is_served(self) -> None:
        self.assertEqual(models_page.provider_of("https://api.groq.com/openai/v1/chat/completions"), "Groq")
        self.assertEqual(models_page.provider_of("https://openrouter.ai/api/v1/chat/completions"), "OpenRouter")
        self.assertEqual(models_page.provider_of("http://127.0.0.1:11434/v1/chat/completions"), "Ollama")
        self.assertEqual(models_page.provider_of("http://127.0.0.1:8792/v1/chat/completions"), "Kosika")

    def test_the_page_says_whether_a_key_is_set_never_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            router = Path(folder) / "router.json"
            router.write_text(json.dumps({"models": {"strong": {"id": "m", "url": "https://api.groq.com/x", "key": "TEST_MODEL_KEY"}},
                                          "routes": {"quick": ["strong"]}}), encoding="utf-8")
            with mock.patch.object(models_page, "router_file", return_value=router), \
                    mock.patch.object(models_page, "ledger_file", return_value=Path(folder) / "none.jsonl"), \
                    mock.patch.dict("os.environ", {"TEST_MODEL_KEY": "secret-value"}):
                page = models_page.page()
        self.assertTrue(page["ok"], page["error"])
        self.assertTrue(page["slots"][0]["key_set"])
        self.assertNotIn("secret-value", json.dumps(page))
        self.assertEqual(page["verdicts"][0]["state"], "unused")


if __name__ == "__main__":
    unittest.main()
