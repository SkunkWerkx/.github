// The pinned companion's checks both Pyodide passes share (suite-pyodide.mjs under Node,
// browser-pyodide.mjs in Chrome), so the two can never drift apart. action.yml copies this file
// beside them.
import { existsSync, readFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";

const SUMS_LINE = /^([0-9a-f]{64}) {2}(.+)$/;

// A companion-mode leg's checkout (PYODIDE_SUITE_COMPANIONS, the root fetch-companions took,
// relative to GITHUB_WORKSPACE) holds the .companions/env it wrote, naming each pinned Pyodide
// wheel (<NAME>_PYODIDE_REQ). Every one must reach the suite through PYODIDE_SUITE_SUMS, or
// micropip would resolve the companion from PyPI and the suite would run against it. Without the
// file (no pin, or release mode) nothing changes. Exits 1, naming the wheel, otherwise.
export function requirePinnedSums() {
  const companionEnv = join(
    resolve(process.env.GITHUB_WORKSPACE ?? process.cwd(), process.env.PYODIDE_SUITE_COMPANIONS || "."),
    ".companions",
    "env",
  );
  if (!existsSync(companionEnv)) return;
  const sumsPath = process.env.PYODIDE_SUITE_SUMS ?? "";
  const listed = new Set(
    (sumsPath ? readFileSync(sumsPath, "utf8").split("\n") : [])
      .map((line) => SUMS_LINE.exec(line))
      .filter(Boolean)
      .map((match) => basename(match[2])),
  );
  for (const line of readFileSync(companionEnv, "utf8").split("\n")) {
    const match = /^[A-Z0-9_]+_PYODIDE_REQ=(.*)$/.exec(line);
    for (const pinned of match ? match[1].split(",").filter(Boolean) : []) {
      const name = basename(pinned);
      if (!sumsPath) {
        console.error(`${companionEnv}: .companions/env pins ${name} but PYODIDE_SUITE_SUMS is not set; micropip would resolve the companion from PyPI`);
        process.exit(1);
      }
      if (!listed.has(name)) {
        console.error(`${companionEnv}: .companions/env pins ${name} but PYODIDE_SUITE_SUMS (${sumsPath}) does not list it`);
        process.exit(1);
      }
    }
  }
}

// SHA256SUMS lines (`<hex>  <dir>/<file>`), by file name, wheels only; empty without a path.
export function readSums(path) {
  const sums = new Map();
  if (!path) return sums;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const match = SUMS_LINE.exec(line);
    if (match && match[2].endsWith(".whl")) sums.set(basename(match[2]), match[1]);
  }
  return sums;
}

// The distribution name a wheel's file name starts with, normalized as micropip lists it.
export const distName = (file) => file.split("-")[0].replace(/[-_.]+/g, "-").toLowerCase();

// Run in Pyodide once the wheel under test is in, with pinned_name (distName) and pinned_source
// (`emfs:/companions/<wheel>`) set: micropip must have the companion from the pin's path. Its
// value is the line each pass prints.
export const PINNED_INSTALLED = `
import micropip
installed = micropip.list()
assert pinned_name in installed, f"{pinned_name} is not installed"
package = installed[pinned_name]
assert package.source == pinned_source, f"Pyodide: found {pinned_name} {package.version} from {package.source}, expected {pinned_source} (the pin)"
f"Pyodide: {pinned_name} {package.version} from {package.source}"
`;
