"use strict";

let source = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  source += chunk;
});
process.stdin.on("end", () => {
  try {
    const request = JSON.parse(source);
    if (
      request.schema !== "kosika.component-call/v1" ||
      request.component !== "example.uppercase" ||
      request.version !== "1.0.0" ||
      request.operation !== "uppercase" ||
      typeof request.input?.text !== "string"
    ) {
      process.stdout.write(
        JSON.stringify({
          schema: "kosika.component-result/v1",
          ok: false,
          error: {code: "input:invalid", message: "Expected input.text"},
        }),
      );
      return;
    }
    process.stdout.write(
      JSON.stringify({
        schema: "kosika.component-result/v1",
        ok: true,
        output: {text: request.input.text.toUpperCase()},
      }),
    );
  } catch (_error) {
    process.stdout.write(
      JSON.stringify({
        schema: "kosika.component-result/v1",
        ok: false,
        error: {code: "input:invalid", message: "Invalid request JSON"},
      }),
    );
  }
});
