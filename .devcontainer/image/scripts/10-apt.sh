# System packages (root): build deps for pyenv / ruby-build / php-build / swiftly, the C toolchain, CLI tools.
source /usr/local/share/hyperforge/lib.sh
apt-get update
# Pull Debian security fixes the base image doesn't have yet (e.g. DSA-6582-1, brotli CVE-2026-111497; PHP's
# curl reaches libbrotli). Every image rebuild re-runs this, so the scheduled rebuild keeps the image patched.
apt-get upgrade -y -o Acquire::Retries=3
apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
  build-essential pkg-config cmake ninja-build autoconf automake libtool bison re2c patch gawk file \
  clang lld llvm libclang-dev clang-format clangd \
  jq zip unzip xz-utils bzip2 zstd ca-certificates gnupg \
  libssl-dev zlib1g-dev libbz2-dev liblzma-dev libreadline-dev libedit-dev libncurses-dev libgdbm-dev \
  libsqlite3-dev libffi-dev uuid-dev tk-dev libyaml-dev libgmp-dev libxml2-dev libxslt1-dev \
  libcurl4-openssl-dev libicu-dev libonig-dev libsodium-dev libzip-dev libpng-dev libjpeg-dev libwebp-dev \
  libtidy-dev libldap-dev libfreetype-dev \
  libpython3-dev libz3-dev libedit2 tzdata \
  shellcheck hyperfine
# gh from GitHub's own apt repo (arm64 + amd64).
fetch https://cli.github.com/packages/githubcli-archive-keyring.gpg /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$DEB_ARCH signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y --no-install-recommends gh
# Stable libclang path for bindgen. Swift ships its own clang, which conflicts with bindgen (CI sets the same vars).
ln -sfn "$(ls -d /usr/lib/llvm-* | sort -V | tail -1)" /usr/local/lib/llvm
rm -rf /var/lib/apt/lists/*
