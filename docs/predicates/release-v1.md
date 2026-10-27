# The release attestation, `release/v1`

Predicate type: `https://github.com/SkunkWerkx/.github/predicates/release/v1`

A path-only identifier owned by SkunkWerkx. It does not resolve; it names this document and
never changes once used. A new field is added here under the same type; a changed meaning
is a `release/v2`.

## What it says

One in-toto statement per release, signed by the consumer repository's own `release.yml` at
the release tag, whose **subjects** are every file the release ships and whose **predicate**
is the release inventory (`release-packages/inventory.json`, schema 2). The statement is
made in the publication gate, before anything is uploaded, and preserved with the packages.

Subjects (about 90 per release) are every file in `inventory.json`:

- the registry packages: the `.nupkg` and `.snupkg`, the nine gems, the 17 wheels, the
  `.crate` with its saved registry request, the signed Maven bundle and the repository files
  inside it (the jar, the sources and javadoc jars, the `.pom`, their signatures), and, from
  a consumer with those bindings, the nine npm tarballs (the root package and one per
  platform) and the CRAN source tarball, which no job uploads (the maintainer submits it);
- the native libraries committed at the tagged commit for Go (`go/staticlib/**`), Swift
  (the artifact bundle and the XCFramework) and PHP (`php/src/native/**`), and the metadata
  staged beside them: the bundle's `info.json` and the XCFramework's `Info.plist`, headers
  and module maps.

Equal bytes under two paths (a static archive Swift and Go both carry) stay two subjects;
`gh attestation verify` matches by digest, so either path verifies.

A second statement of the same type and predicate covers the **finalized** NuGet package,
the bytes nuget.org serves after adding its repository signature, made by the publisher after
the push.

## The predicate

`inventory.json` as written by `release_inventory.py create --natives`, schema 2:

| Field | Meaning |
| --- | --- |
| `schema` | `2` |
| `repository` | `SkunkWerkx/<Repo>` |
| `crate`, `version`, `tag` | the lowercase core name, the bare version, `v<version>` |
| `source_sha` | A, the preparation commit the natives and wheels were built from |
| `staging_sha` | B, the tagged commit: A plus the committed natives |
| `ci_run_id` | the release-mode `ci.yml` run at A |
| `package_run_id` | the `release.yml` run that packed and attested |
| `forge_sha` | the Forge commit that run used |
| `files[]` | `path`, `size`, `sha256`, `location` (`packages` or `source`), `source_sha` (A for wheels and native libraries, B for packages assembled at the tag), `signer_repo` (the repository whose workflow signed the file's build provenance: `SkunkWerkx/.github` for Forge-built files, the consumer for the gems, the npm tarballs and the CRAN tarball). Both are absent for the staged metadata, which carries no build provenance; the release attestation is its only binding |

`inventory.json` itself carries SLSA build provenance at B, made in the same gate.

## Verifying

Any shipped file, from the tag alone:

```sh
gh attestation verify <file> --repo SkunkWerkx/<Repo> \
  --source-ref refs/tags/vX.Y.Z \
  --predicate-type https://github.com/SkunkWerkx/.github/predicates/release/v1
```

`--source-ref` holds the signing workflow to the tag; `--source-digest <B>` holds it to the
commit, which is what the release's own machine checks use (a dispatch at the tag has the same
ref, so the two agree). `--format json` prints the statement; its `predicate` is the
inventory, so the file's `path`, `size` and build `source_sha` can be read back from it.
Without `--predicate-type`, `gh` looks for SLSA build provenance instead, which Forge-built
files carry under the Forge's identity:

```sh
gh attestation verify <file> --repo SkunkWerkx/<Repo> --signer-repo SkunkWerkx/.github --source-digest <A>
```

Negative controls, each of which must fail: the default predicate type on a wheel or a Go
archive (built at A on a branch, so `--source-ref refs/tags/...` is not theirs); a wrong
`--source-ref`; a file whose bytes are not in the inventory.

## Where it is made and checked

- Made: `.github/actions/release-gate` (`actions/attest` with `subject-checksums` derived
  from the inventory by `release_inventory.py checksums`, `predicate-path` the inventory) and
  `.github/actions/publish-nuget` for the finalized package.
- Checked: `release_inventory.py verify` for every schema 2 inventory, in the gate, every
  publisher and the finalizer, by downloading the statement once and verifying each file
  against it offline (`gh attestation download`, then `verify --bundle`), comparing the
  returned predicate to the inventory on disk. Recovery runs verify; they never re-attest.
