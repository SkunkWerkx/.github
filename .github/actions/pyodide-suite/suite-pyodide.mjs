// Installs a Pyodide wheel into Pyodide under Node, checks it the way the native wheel legs
// do, then runs the binding's own pytest suite inside the same interpreter.
//
//     node suite-pyodide.mjs <crate> <version> <wheel> <tests-dir>
//
// Run from a directory where `pyodide` is installed (`npm install pyodide@<version>`): ESM
// resolves the import from the script's own location, so action.yml copies this file there.
// Pyodide fetches micropip and pytest from its CDN on first use. Exits non-zero if the
// wheel does not install, does not load the native backend, reports a different core
// version, or any test fails.
//
// The tests directory is copied to /work/tests. A conftest.py that puts the checkout's
// ../src on sys.path (both Hyper* Python suites have one) adds /work/src, which does not
// exist, so the installed wheel is what the suite imports.
//
// PYODIDE_SUITE_DEPS, a directory of Pyodide wheels the one under test depends on, are
// installed first, each from /companions/<wheel> in the emscripten filesystem. With
// PYODIDE_SUITE_SUMS (a pinned companion's SHA256SUMS, companion-pins), every wheel it lists
// must be among them with that hash, which is checked again inside the interpreter on the
// bytes micropip installs, and micropip must report the companion installed from that path
// once the wheel under test is in: its own requirement on the companion never reached PyPI.
import { loadPyodide } from "pyodide";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { PINNED_INSTALLED, distName, readSums, requirePinnedSums } from "./companion-guard.mjs";

const [crate, want, wheel, testsDir] = process.argv.slice(2);
if (!testsDir) {
  console.error("usage: node suite-pyodide.mjs <crate> <version> <wheel> <tests-dir>");
  process.exit(2);
}

// The pinned companion's wheels must all reach this suite (companion-guard.mjs).
requirePinnedSums();

function copyTree(py, from, to) {
  py.FS.mkdirTree(to);
  for (const name of readdirSync(from)) {
    if (name === "__pycache__" || name === ".pytest_cache") continue;
    const src = join(from, name);
    if (statSync(src).isDirectory()) copyTree(py, src, `${to}/${name}`);
    else py.FS.writeFile(`${to}/${name}`, readFileSync(src));
  }
}

try {
  const depsDir = process.env.PYODIDE_SUITE_DEPS ? resolve(process.env.PYODIDE_SUITE_DEPS) : "";
  const deps = depsDir ? readdirSync(depsDir).filter((name) => name.endsWith(".whl")).sort() : [];
  const sums = readSums(process.env.PYODIDE_SUITE_SUMS);
  for (const name of sums.keys()) {
    if (!deps.includes(name)) throw new Error(`the pinned ${name} is not among the dependency wheels (${depsDir || "none given"})`);
  }

  const py = await loadPyodide();
  console.log(`Pyodide ${py.version}`);
  await py.loadPackage(["micropip", "pytest"]);
  const micropip = py.pyimport("micropip");

  py.FS.mkdirTree("/companions");
  for (const dep of deps) {
    const bytes = readFileSync(join(depsDir, dep));
    const path = `/companions/${dep}`;
    const want = sums.get(dep);
    if (want && createHash("sha256").update(bytes).digest("hex") !== want) {
      throw new Error(`${dep} differs from SHA256SUMS (${want})`);
    }
    py.FS.writeFile(path, bytes);
    if (want) {
      py.globals.set("pinned_path", path);
      py.globals.set("pinned_sha256", want);
      py.runPython(`
import hashlib
with open(pinned_path, "rb") as f:
    found = hashlib.sha256(f.read()).hexdigest()
assert found == pinned_sha256, f"{pinned_path}: sha256 {found}, not SHA256SUMS' {pinned_sha256}"
`);
    }
    await micropip.install(`emfs:${path}`);
  }

  const local = `/tmp/${basename(wheel)}`;
  py.FS.writeFile(local, readFileSync(wheel));
  await micropip.install(`emfs:${local}`);

  // Each pinned companion, as micropip has it once the wheel under test (which requires it) is in.
  for (const [dep, want] of sums) {
    py.globals.set("pinned_name", distName(dep));
    py.globals.set("pinned_source", `emfs:/companions/${dep}`);
    console.log(py.runPython(PINNED_INSTALLED) + `, sha256 ${want}`);
  }

  py.globals.set("crate", crate);
  py.globals.set("want", want);
  console.log(py.runPython(`
import importlib, sys
mod = importlib.import_module(crate)
assert mod.native_version() == want, mod.native_version()
f"{crate} {mod.native_version()}, Python {sys.version.split()[0]} on {sys.platform}"
`));

  copyTree(py, testsDir, "/work/tests");
  const rc = py.runPython(`
import pytest
int(pytest.main(["-ra", "-p", "no:cacheprovider", "--rootdir", "/work", "/work/tests"]))
`);
  process.exit(rc);
} catch (error) {
  console.error(error?.message ?? error);
  process.exit(1);
}
