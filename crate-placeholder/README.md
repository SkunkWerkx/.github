# crate-placeholder

crates.io Trusted Publishing can be configured only for a crate that already exists. Unlike
PyPI and RubyGems, crates.io has no pending publishers. So a new Hyper\* crate gets one
hand-published version before its first release: this empty crate, renamed, as `0.0.1`,
yanked straight away. Every real version after that, `0.7.0` or whatever the first is,
goes out through `hyper-publish-crate.yml` with an OIDC token and an attestation, the same
as every other release.

```
# a crates.io API token with the publish-new and yank scopes, used once and then revoked
export CARGO_REGISTRY_TOKEN=...
./reserve-crate.sh hypertabular SkunkWerkx/HyperTabular
```

The script publishes a copy from a temporary directory, so this crate's own name and
`publish = false` never leave this repository. Then:

1. On crates.io, open the crate's **Settings → Trusted Publishing**, add a GitHub publisher
   with the repository's owner and name and the workflow `release.yml`, the same as the
   existing Hyper\* crates.
2. Revoke the token.
3. Tag the release. The placeholder stays on crates.io as a yanked `0.0.1`, which nothing
   resolves to.
