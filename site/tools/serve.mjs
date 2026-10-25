#!/usr/bin/env node
/**
 * Serve the site locally, with what GitHub Pages gives you for free:
 * byte ranges (so the film can be scrubbed and seeked) and the right
 * content types. No dependencies.
 *
 *   node tools/serve.mjs            # http://127.0.0.1:8099/
 *   node tools/serve.mjs 4000       # another port
 */
import http from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = Number(process.argv[2] || process.env.PORT || 8099);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".ttf": "font/ttf",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".py": "text/plain; charset=utf-8"
};

const server = http.createServer(async (req, res) => {
  let rel;
  try {
    rel = decodeURIComponent(new URL(req.url, "http://x").pathname);
  } catch {
    res.writeHead(400).end("bad path");
    return;
  }
  let file = path.join(ROOT, rel);
  // never serve outside the site folder
  if (!file.startsWith(ROOT + path.sep) && file !== ROOT) {
    res.writeHead(403).end("outside");
    return;
  }
  try {
    let info = await stat(file);
    if (info.isDirectory()) {
      file = path.join(file, "index.html");
      info = await stat(file);
    }
    const type = TYPES[path.extname(file).toLowerCase()] || "application/octet-stream";
    const range = req.headers.range;
    if (range) {
      const m = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (m) {
        const size = info.size;
        let start = m[1] === "" ? size - Number(m[2]) : Number(m[1]);
        let end = m[1] === "" || m[2] === "" ? size - 1 : Number(m[2]);
        start = Math.max(0, start);
        end = Math.min(size - 1, end);
        if (start > end) {
          res.writeHead(416, { "content-range": `bytes */${size}` }).end();
          return;
        }
        res.writeHead(206, {
          "content-type": type,
          "content-length": end - start + 1,
          "content-range": `bytes ${start}-${end}/${size}`,
          "accept-ranges": "bytes",
          "cache-control": "no-cache"
        });
        createReadStream(file, { start, end }).pipe(res);
        return;
      }
    }
    res.writeHead(200, {
      "content-type": type,
      "content-length": info.size,
      "accept-ranges": "bytes",
      "cache-control": "no-cache"
    });
    if (req.method === "HEAD") { res.end(); return; }
    createReadStream(file).pipe(res);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" }).end("not found");
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`site on http://127.0.0.1:${PORT}/  (root ${ROOT})`);
});
