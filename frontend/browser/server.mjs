import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const assets = resolve(
  "../custom_components/conditional_notifications/frontend",
);
createServer(async (request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  const asset = path.match(/^\/assets\/([a-z-]+\.js)$/);
  const target = asset
    ? resolve(assets, asset[1])
    : path === "/"
      ? resolve("browser/fixture.html")
      : null;
  if (!target) {
    response.writeHead(404).end();
    return;
  }
  try {
    response.setHeader("Content-Type", asset ? "text/javascript" : "text/html");
    response.end(await readFile(target));
  } catch {
    response.writeHead(404).end();
  }
}).listen(8873, "127.0.0.1");
