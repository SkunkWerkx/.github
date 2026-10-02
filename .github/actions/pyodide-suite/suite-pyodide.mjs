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
import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { basename, join } from "node:path";

const [crate, want, wheel, testsDir] = process.argv.slice(2);
if (!testsDir) {
  console.error("usage: node suite-pyodide.mjs <crate> <version> <wheel> <tests-dir>");
  process.exit(2);
}

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
  const py = await loadPyodide();
  console.log(`Pyodide ${py.version}`);
  await py.loadPackage(["micropip", "pytest"]);

  const local = `/tmp/${basename(wheel)}`;
  py.FS.writeFile(local, readFileSync(wheel));
  await py.pyimport("micropip").install(`emfs:${local}`);

  py.globals.set("crate", crate);
  py.globals.set("want", want);
  console.log(py.runPython(`
import importlib, sys
mod = importlib.import_module(crate)
assert mod.BACKEND == "native", mod.BACKEND
assert mod.native_version() == want, mod.native_version()
f"{crate} {mod.native_version()} on the {mod.BACKEND} backend, Python {sys.version.split()[0]} on {sys.platform}"
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
