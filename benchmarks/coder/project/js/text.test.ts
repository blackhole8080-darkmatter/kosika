import { test } from "node:test";
import assert from "node:assert/strict";
import { shout } from "./text.ts";

test("shout makes it loud", () => {
  assert.equal(shout("hi"), "HI!");
});
