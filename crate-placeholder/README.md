# crate-placeholder

crates.io Trusted Publishing can be configured only for a crate that already exists. PyPI
and RubyGems have pending publishers and a nuget.org policy belongs to the account, not to
a package, so crates.io is the one registry whose first version is published by hand. A new
Hyper\* crate gets that version before its first release: this empty crate, renamed, as
`0.0.1`, pushed by token and yanked straight away. Every real version after that, the first
included, goes out through the consumer's `release.yml` (which calls `hyper-publish-crate.yml`)
with an OIDC token, the same as every other release.

```
# a crates.io API token with the publish-new and yank scopes, used once and then revoked
export CARGO_REGISTRY_TOKEN=...
./reserve-crate.sh hypertabular SkunkWerkx/HyperTabular
```

The script publishes a copy from a temporary directory, so this crate's own name and
`publish = false` never leave this repository. Then:

1. On crates.io, open the crate's **Settings → Trusted Publishing**, add a GitHub publisher
   with the repository's owner and name and the workflow `release.yml`, the same as the
   existing Hyper\* crates. crates.io matches the token's `workflow_ref`, the top-level
   calling workflow, so it is `release.yml`, never `hyper-publish-crate.yml`.
2. Revoke the token.
3. Tag the release. The placeholder stays on crates.io as a yanked `0.0.1`, which nothing
   resolves to.
