// The npm side of a pinned companion inside a container that installs afresh (the companion-pins
// spec, "Isolated environments get the companion explicitly"): run by the image's own Node, or
// Bun where the image has no Node, through companion_container.sh's companion_npm, after the
// host's tarballs (.companions/<name>-npm/, with companion_wire.py's wired.json) and the leg's
// artifacts were copied under <work>. Node's standard library and the image's tar only: no npm
// package, no Python.
//
//   companion_npm.mjs wire <work> <package-dir>
//       Each dependency the host pointed at a pinned tarball (`file:` a host path, absent here)
//       pointed at the <work> copy, the lock regenerated (bun install --lockfile-only beside a
//       bun.lock, npm install --package-lock-only beside a package-lock.json), then the lock proven
//       as `origin` does.
//   companion_npm.mjs origin <work> <package-dir> [--installed]
//       The lock resolves each wired package from its <work> tarball (`file:`), with the
//       integrity the host's pack gave it and the file has; the tarball holds the leg's
//       payload byte for byte as SHA256SUMS lists it. --installed, after the install and before the
//       suite: the package resolves from <package-dir>/node_modules, whose payload is the pin's
//       and whose package.json and files are the tarball's, whole.
//   companion_npm.mjs unpack <work> <package-dir>
//       For an image that runs a suite without installing (Bun's): each wired package's tarball, its integrity checked,
//       unpacked into <package-dir>/node_modules, then proven as --installed does.
//
// Every failure prints `::error::npm (<package-dir>): found <origin>, expected <origin>` and
// exits 1.
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { basename, dirname, join, relative, resolve } from 'node:path'

const INSTALLED = ['dependencies', 'devDependencies', 'optionalDependencies']

class OriginError extends Error {}

const [step, workArg, dirArg, ...flags] = process.argv.slice(2)
if (!['wire', 'origin', 'unpack'].includes(step) || !workArg || !dirArg) {
  console.error('usage: companion_npm.mjs wire|origin|unpack <work> <package-dir> [--installed]')
  process.exit(2)
}
// Real paths: npm writes a lock's `file:` relative to the real directory, and Node resolves
// through symlinks.
const work = realpathSync(resolve(workArg))
const dir = realpathSync(resolve(dirArg))
const binding = `npm (${dir})`

const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))
const integrity = (bytes) => `sha512-${createHash('sha512').update(bytes).digest('base64')}`
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')

// .companions/env as the host wrote it: the companion's prefix (from its *_NPM_REQ line),
// version and artifacts directory, each path relative to the checkout.
const env = Object.fromEntries(
  readFileSync(join(work, '.companions', 'env'), 'utf8')
    .split('\n')
    .filter((line) => line.includes('='))
    .map((line) => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]),
)
const reqs = Object.keys(env).filter((key) => key.endsWith('_NPM_REQ'))
if (reqs.length !== 1) {
  console.log(`::error::${work}/.companions/env has ${reqs.length} *_NPM_REQ lines: the host never wired npm`)
  process.exit(1)
}
const prefix = reqs[0].slice(0, -'_NPM_REQ'.length)
const name = prefix.toLowerCase()
const version = env[`${prefix}_VERSION`]
// A file the host wrote and the container copied: its absence is named, not a stack trace.
const hostFile = (rel) => {
  if (!existsSync(join(work, rel))) {
    console.log(`::error::${binding}: found no ${rel}, expected it as the host wrote it (companion_wire.py npm, fetch-companions)`)
    process.exit(1)
  }
  return join(work, rel)
}
const artifactsDir = env[`${prefix}_ARTIFACTS`] ?? ''
const record = readJson(hostFile(`.companions/${name}-npm/wired.json`))
const sums = Object.fromEntries(
  readFileSync(hostFile(`${artifactsDir}/SHA256SUMS`), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => [line.slice(66), line.slice(0, 64)]),
)

// The wired packages this manifest installs (its peer section installs nothing of its own).
const manifest = readJson(join(dir, 'package.json'))
const wired = Object.keys(record.packages ?? {}).filter((dep) =>
  INSTALLED.some((section) => manifest[section] && dep in manifest[section]),
)

const expected = (dep) =>
  `${dep} ${version} from file:${record.packages[dep].tarball} (the pinned package packed with this leg's ` +
  `artifacts), integrity ${record.packages[dep].integrity}`

// One file of a tarball, by tar itself: busybox's in Alpine, GNU's or bsdtar elsewhere.
const member = (tarball, file) => {
  try {
    return execFileSync('tar', ['-xzOf', tarball, `package/${file}`], { maxBuffer: 1 << 30, stdio: ['ignore', 'pipe', 'ignore'] })
  } catch {
    return null
  }
}

const proveTarball = (dep) => {
  const wiredDep = record.packages[dep]
  const tarball = join(work, wiredDep.tarball)
  const actual = existsSync(tarball) ? integrity(readFileSync(tarball)) : 'missing'
  if (actual !== wiredDep.integrity) {
    throw new OriginError(`${dep}'s tarball with integrity ${actual}, not the packed ${wiredDep.integrity}`)
  }
  for (const path of wiredDep.payload) {
    const bytes = member(tarball, basename(path))
    if (bytes === null) {
      throw new OriginError(`${dep} from a tarball without ${basename(path)} (packed without the job-local files list)`)
    }
    if (sha256(bytes) !== sums[path]) {
      throw new OriginError(`${dep}'s ${basename(path)} with sha256 ${sha256(bytes)}, not SHA256SUMS' ${sums[path] ?? 'absent'}`)
    }
  }
  return tarball
}

// Bun's text lock: JSON with trailing commas. A value is [`name@version`, registry, metadata,
// integrity], or [`name@path`, metadata, integrity] for a tarball.
const usesBun = existsSync(join(dir, 'bun.lock'))
const parseBunLock = (text) => JSON.parse(text.replace(/("(?:[^"\\]|\\.)*")|,(\s*[}\]])/g, (all, string, close) => string ?? close))
const lockEntry = (dep) => {
  const lockPath = join(dir, usesBun ? 'bun.lock' : 'package-lock.json')
  if (!existsSync(lockPath)) return undefined
  if (!usesBun) return (readJson(lockPath).packages ?? {})[`node_modules/${dep}`]
  const value = (parseBunLock(readFileSync(lockPath, 'utf8')).packages ?? {})[dep]
  if (!value) return undefined
  const spec = value[0].slice(value[0].indexOf('@', 1) + 1)
  const isPath = /^(file:|\.{0,2}\/|[A-Za-z]:[\\/])/.test(spec)
  return {
    version: isPath ? version : spec,
    resolved: isPath ? `file:${spec.replace(/^file:/, '')}` : typeof value[1] === 'string' && value[1] ? value[1] : 'the registry',
    integrity: value.find((item, index) => index > 0 && typeof item === 'string' && item.startsWith('sha')),
  }
}

const proveLock = (dep) => {
  const tarball = proveTarball(dep)
  const entry = lockEntry(dep)
  if (!entry) throw new OriginError(`no node_modules/${dep} entry in ${usesBun ? 'bun.lock' : 'package-lock.json'}`)
  const resolved = String(entry.resolved ?? '')
  if (!resolved.startsWith('file:')) throw new OriginError(`${dep} ${entry.version} from ${resolved || 'nowhere'} (a registry)`)
  if (resolve(dir, resolved.slice('file:'.length)) !== tarball) throw new OriginError(`${dep} ${entry.version} from ${resolved}`)
  if (entry.integrity !== record.packages[dep].integrity) {
    throw new OriginError(`${dep} from ${resolved} with integrity ${entry.integrity}`)
  }
  console.log(`${binding}: ${dep} ${entry.version} from ${resolved}, integrity ${entry.integrity}`)
}

// Every regular file under base, as `/`-joined relative paths, a nested node_modules aside.
const filesUnder = (base, sub = '') =>
  readdirSync(join(base, sub), { withFileTypes: true }).flatMap((entry) => {
    const rel = sub ? `${sub}/${entry.name}` : entry.name
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : filesUnder(base, rel)
    return entry.isFile() ? [rel] : []
  })

// node_modules/<dep> is the tarball whole: its package.json names dep at the pin's version, and
// its files are the tarball's package/ members, byte for byte, none missing and none added.
const proveWhole = (dep, target) => {
  const manifestPath = join(target, 'package.json')
  let installed = {}
  try {
    installed = readJson(manifestPath)
  } catch {}
  if (installed?.name !== dep || installed?.version !== version) {
    throw new OriginError(`${installed?.name} ${installed?.version} installed (${relative(dir, manifestPath)})`)
  }
  const unpacked = mkdtempSync(join(tmpdir(), 'companion-npm-'))
  try {
    execFileSync('tar', ['-xzf', join(work, record.packages[dep].tarball), '-C', unpacked])
    const packedRoot = join(unpacked, 'package')
    const packed = new Set(filesUnder(packedRoot))
    // Bun leaves .bun-tag at the package's top, recording the tarball it installed.
    const present = filesUnder(target).filter((rel) => !(usesBun && rel === '.bun-tag'))
    for (const rel of present) {
      if (!packed.has(rel)) throw new OriginError(`${relative(dir, join(target, rel))}, which the tarball does not hold`)
      if (!readFileSync(join(target, rel)).equals(readFileSync(join(packedRoot, rel)))) {
        throw new OriginError(`${relative(dir, join(target, rel))} unlike the tarball's package/${rel}`)
      }
    }
    for (const rel of [...packed].sort()) {
      if (!present.includes(rel)) throw new OriginError(`${relative(dir, target)} without the tarball's package/${rel}`)
    }
  } finally {
    rmSync(unpacked, { recursive: true, force: true })
  }
}

const proveInstalled = (dep) => {
  const target = join(dir, 'node_modules', dep)
  let where
  try {
    where = dirname(createRequire(join(dir, 'index.js')).resolve(`${dep}/package.json`))
  } catch (error) {
    throw new OriginError(`no ${dep} resolvable from ${dir}: ${error.message.split('\n')[0]}`)
  }
  if (where !== target) throw new OriginError(`${dep} resolving to ${where}`)
  for (const path of record.packages[dep].payload) {
    const file = join(target, basename(path))
    const digest = existsSync(file) ? sha256(readFileSync(file)) : 'missing'
    if (digest !== sums[path]) {
      throw new OriginError(`${relative(dir, file)} with sha256 ${digest}, not SHA256SUMS' ${sums[path] ?? 'absent'}`)
    }
  }
  proveWhole(dep, target)
  console.log(`${binding}: ${dep} installed in node_modules with the pinned ${record.packages[dep].payload.map((p) => basename(p)).join(', ')}`)
}

let current = null
try {
  if (wired.length === 0) {
    current = Object.keys(record.packages ?? {})[0] ?? `${name}'s package`
    throw new OriginError(`${join(dir, 'package.json')} installing no wired package`)
  }
  if (step === 'wire') {
    for (const dep of wired) {
      current = dep
      const tarball = join(work, record.packages[dep].tarball)
      for (const section of INSTALLED) {
        if (manifest[section] && dep in manifest[section]) manifest[section][dep] = `file:${tarball}`
      }
    }
    writeFileSync(join(dir, 'package.json'), `${JSON.stringify(manifest, null, 2)}\n`)
    console.log(`${binding}: ${wired.map((dep) => `${dep} = file:${join(work, record.packages[dep].tarball)}`).join(', ')}`)
    if (usesBun) {
      execFileSync('bun', ['install', '--lockfile-only', '--ignore-scripts'], { cwd: dir, stdio: 'inherit' })
    } else {
      execFileSync('npm', ['install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund'], {
        cwd: dir,
        stdio: 'inherit',
      })
    }
  }
  for (const dep of wired) {
    current = dep
    if (step === 'unpack') {
      const tarball = proveTarball(dep)
      const target = join(dir, 'node_modules', dep)
      rmSync(target, { recursive: true, force: true })
      mkdirSync(target, { recursive: true })
      execFileSync('tar', ['-xzf', tarball, '-C', target, '--strip-components=1'])
      proveInstalled(dep)
    } else {
      proveLock(dep)
      if (flags.includes('--installed')) proveInstalled(dep)
    }
  }
} catch (error) {
  if (!(error instanceof OriginError)) throw error
  console.log(`::error::${binding}: found ${error.message}, expected ${current in (record.packages ?? {}) ? expected(current) : current}`)
  process.exit(1)
}
