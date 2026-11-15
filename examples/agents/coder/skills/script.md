# How code is added to a JavaScript or TypeScript file
When: javascript, typescript, .js, .ts, .mjs, .tsx, .jsx, node

This is for JavaScript and TypeScript files only. Ignore it for other
languages.

Keep everything the file already has, unchanged, and add the new code after
it. Write modules the modern way: `export function name(...)` and
`import { name } from "./file.ts"`. Node needs the file's whole name in an
import, ending and all: `./calc.ts`, never `./calc` and never `./calc.js`
for a file that is `calc.ts`. Follow the file's own style: its quotes, its
semicolons, its indentation. A script that a web page loads with a plain
script tag is not a module: write no `import` or `export` there.

In TypeScript, give every parameter and every return value a type, and do
not use `any`. Node runs TypeScript by removing the types, so write only
types that can be removed: no `enum`, no `namespace`, no parameter
properties in a constructor. Use a union of texts, such as
`"low" | "high"`, where an enum would go.

Do not print from a function that is meant to return a value. Add no
library: use what the language and Node already have.
