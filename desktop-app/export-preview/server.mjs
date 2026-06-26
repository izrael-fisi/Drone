import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 1420);

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
};

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0] || "/");
  const normalized = normalize(decoded).replace(/^(\.\.[/\\])+/, "").replace(/^[/\\]+/, "");
  const candidate = resolve(join(root, normalized));
  return candidate.startsWith(root) ? candidate : join(root, "index.html");
}

async function fileOrIndex(urlPath) {
  const candidate = safePath(urlPath === "/" ? "/index.html" : urlPath);
  try {
    const info = await stat(candidate);
    if (info.isFile()) return candidate;
  } catch {
    // Fall back to the SPA entrypoint below.
  }
  return join(root, "index.html");
}

const server = createServer(async (request, response) => {
  try {
    const filePath = await fileOrIndex(request.url || "/");
    const body = await readFile(filePath);
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": mimeTypes[extname(filePath).toLowerCase()] || "application/octet-stream",
    });
    response.end(body);
  } catch (error) {
    response.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
    response.end(`Drone Vision Nav preview server error:\n${error}`);
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log("");
  console.log("Drone Vision Nav UI preview is running.");
  console.log(`Open: http://127.0.0.1:${port}/`);
  console.log("");
  console.log("Press Ctrl+C to stop.");
});
