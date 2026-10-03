"""Build and run a Kosika program from Python.

    python examples/sdk_hello.py

The model answer below is a scripted stand-in, so this runs offline. To use a
real local model, bind ``OpenAICompatibleProvider(url, model_name)`` instead.
"""

from __future__ import annotations

import json

from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.sdk import App, ref


def build() -> App:
    app = App()
    question = app.record("Question", text="Text")
    answer = app.record("Answer", text="Text")
    assistant = app.model("assistant", input=question, output=answer)
    main = app.workflow("main", output=answer, question=question)
    main.use_model("respond", assistant, text=ref("question.text"))
    return app


def main() -> None:
    app = build()
    print(app.to_source())
    scripted = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": "Hello from a checked program."}))]
    )
    result = app.run(
        inputs={"question": {"text": "Say hello."}},
        model_bindings={"assistant": scripted},
    )
    print(result)


if __name__ == "__main__":
    main()
