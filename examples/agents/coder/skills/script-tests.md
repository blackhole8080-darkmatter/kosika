# How a JavaScript or TypeScript test file is written
When: javascript, typescript, .js, .ts, .mjs, .tsx, .jsx, node
Files: *.test.ts, *.test.js, *.test.mjs
Check: contains "node:test"
Check: contains "assert"

This is for JavaScript and TypeScript files only. Ignore it for other
languages.

Tests are run by Node itself, with no library to install. The tests for
`calc.ts` go in `calc.test.ts`, beside it; for `calc.js`, in `calc.test.js`.
A test file begins:

    import { test } from "node:test";
    import assert from "node:assert/strict";
    import { add } from "./calc.ts";

and each test is written at the left margin:

    test("add puts two numbers together", () => {
      assert.equal(add(1, 2), 3);
    });

Use `assert.equal` for plain values, `assert.deepEqual` for lists and
objects, and `assert.throws(() => ...)` for an error. Test the normal case
and at least one edge case, such as zero, an empty text or a negative
number. Keep every test the file already has.
