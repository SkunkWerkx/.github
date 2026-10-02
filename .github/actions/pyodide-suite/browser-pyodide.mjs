// The same checks as suite-pyodide.mjs, in a real headless browser: the wheel installed by
// micropip in a Chromium page, its version checked, and the binding's whole pytest suite run
// there.
//
//     node browser-pyodide.mjs <crate> <version> <wheel> <tests-dir>
//
// Everything is served from 127.0.0.1 by this script: Pyodide's own files from the npm
// package beside it, the wheel, the test files and the page itself, so the browser needs no
// network. Run it after suite-pyodide.mjs in the same directory — that run is what caches
// micropip, pytest and pytest's dependencies into node_modules/pyodide (the npm package
// carries only the runtime; under Node, Pyodide fetches a missing package from its CDN once
// and keeps it, a browser has no such fallback).
//
// The browser is whatever Chrome or Chromium the machine already has: $CHROME_PATH, else
// the first of google-chrome, google-chrome-stable, chromium, chromium-browser on PATH.
// puppeteer-core drives it and never downloads one.
import puppeteer from "puppeteer-core";
import { createServer } from "node:http";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { basename, delimiter, dirname, extname, join, relative, resolve, sep } from "node:path";
import { createRequire } from "node:module";

const [crate, want, wheel, testsDir] = process.argv.slice(2);
if (!testsDir) {
  console.error("usage: node browser-pyodide.mjs <crate> <version> <wheel> <tests-dir>");
  process.exit(2);
}

const pyodideDir = dirname(createRequire(import.meta.url).resolve("pyodide/package.json"));
for (const pkg of ["micropip", "pytest"]) {
  if (!readdirSync(pyodideDir).some((f) => f.startsWith(`${pkg}-`) && f.endsWith(".whl"))) {
    console.error(`${pkg} is not cached in ${pyodideDir}; run suite-pyodide.mjs here first.`);
    process.exit(1);
  }
}

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    for (const dir of (process.env.PATH ?? "").split(delimiter)) {
      if (dir && existsSync(join(dir, name))) return join(dir, name);
    }
  }
  throw new Error("No Chrome or Chromium found; set CHROME_PATH.");
}

function listTree(root, dir = root) {
  const files = [];
  for (const name of readdirSync(dir)) {
    if (name === "__pycache__" || name === ".pytest_cache") continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) files.push(...listTree(root, path));
    else files.push({ path: relative(root, path).split(sep).join("/"), data: readFileSync(path).toString("base64") });
  }
  return files;
}

const wheelName = basename(wheel);
const page_html = `<!doctype html><meta charset="utf-8"><title>${crate} in Pyodide</title>
<script src="/pyodide/pyodide.js"></script>
<script>
window.result = (async () => {
  const lines = [];
  // Raw writes rather than "batched": pytest's progress dots arrive without newlines, and
  // batched mode would put each on a line of its own.
  const decoder = new TextDecoder();
  let text = "";
  const write = (bytes) => { text += decoder.decode(bytes, { stream: true }); return bytes.length; };
  try {
    const py = await loadPyodide({ indexURL: "/pyodide/" });
    py.setStdout({ write });
    py.setStderr({ write });
    await py.loadPackage(["micropip", "pytest"]);
    await py.pyimport("micropip").install(new URL("/wheel/${wheelName}", location.href).href);
    const tests = await (await fetch("/tests.json")).json();
    for (const { path, data } of tests) {
      const target = "/work/tests/" + path;
      py.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
      py.FS.writeFile(target, Uint8Array.from(atob(data), (c) => c.charCodeAt(0)));
    }
    py.globals.set("crate", ${JSON.stringify(crate)});
    py.globals.set("want", ${JSON.stringify(want)});
    lines.push(py.runPython(\`
import importlib, sys
mod = importlib.import_module(crate)
assert mod.BACKEND == "native", mod.BACKEND
assert mod.native_version() == want, mod.native_version()
f"{crate} {mod.native_version()} on the {mod.BACKEND} backend, Python {sys.version.split()[0]} on {sys.platform}"
\`), navigator.userAgent);
    const rc = py.runPython(\`
import pytest
int(pytest.main(["-ra", "-p", "no:cacheprovider", "--rootdir", "/work", "/work/tests"]))
\`);
    return { rc, output: lines.join("\\n") + "\\n" + text };
  } catch (error) {
    lines.push(String(error && error.message || error));
    return { rc: 1, output: lines.join("\\n") + "\\n" + text };
  }
})();
</script>`;

const types = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".wasm": "application/wasm", ".json": "application/json", ".zip": "application/zip",
  ".whl": "application/zip", ".tar": "application/x-tar",
};

const server = createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  let body;
  let type = "application/octet-stream";
  if (url === "/" || url === "/index.html") {
    body = page_html;
    type = types[".html"];
  } else if (url === "/tests.json") {
    body = JSON.stringify(listTree(resolve(testsDir)));
    type = types[".json"];
  } else if (url === `/wheel/${wheelName}`) {
    body = readFileSync(wheel);
    type = types[".whl"];
  } else if (url.startsWith("/pyodide/")) {
    const file = resolve(pyodideDir, url.slice("/pyodide/".length));
    if (file.startsWith(pyodideDir + sep) && existsSync(file) && statSync(file).isFile()) {
      body = readFileSync(file);
      type = types[extname(file)] ?? type;
    }
  }
  if (body === undefined) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { "content-type": type }).end(body);
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${server.address().port}`;

let rc = 1;
let browser;
try {
  const executablePath = findChrome();
  // --no-sandbox: Ubuntu 24.04+ runner images restrict the unprivileged user namespaces
  // Chrome's sandbox needs. The page only ever loads from this script's own server.
  browser = await puppeteer.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
  console.log(`${executablePath}: ${await browser.version()}`);
  const page = await browser.newPage();
  page.setDefaultTimeout(300_000);
  // Hold the page to this server: a request anywhere else fails it, so the pass cannot
  // quietly start depending on a CDN.
  const offsite = [];
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (request.url().startsWith(`${origin}/`)) return request.continue();
    offsite.push(request.url());
    return request.abort();
  });
  await page.goto(`${origin}/`);
  const result = await page.evaluate(() => window.result);
  console.log(result.output);
  rc = result.rc;
  if (offsite.length) {
    console.error(`The page requested ${offsite.length} URL(s) off 127.0.0.1:\n${offsite.join("\n")}`);
    rc = rc || 1;
  }
} catch (error) {
  console.error(error?.message ?? error);
} finally {
  await browser?.close();
  server.close();
}
process.exit(rc);
