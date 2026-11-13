# Python (vscode): uv only. Prebuilt CPython from python-build-standalone, no compiling, no pyenv.
#   - 3.14.x with the GIL, FIXED: our last GIL line (policy, not CPython's: 3.15+ still default to the GIL).
#     Owns python / python3.
#   - newest free-threaded release, FLOATS: 3.15 brought abi3t, the free-threaded stable ABI (PEP 803);
#     3.16 replaces it when it ships (October 2027). Run as python3.Nt.
# Everything under /opt/uv (UV_* in the Dockerfile), nothing in $HOME. uv's Pythons are externally managed
# (PEP 668): projects use `uv venv` / `uv run`; CLI tools are `uv tool`s.
source /usr/local/share/hyperforge/lib.sh
curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/opt/uv/bin UV_NO_MODIFY_PATH=1 sh
FT=$(uv python list --only-downloads --all-versions | grep -oE 'cpython-3\.[0-9]+\.[0-9]+\+freethreaded' | sort -uV | tail -1)
FT_MINOR=$(sed -E 's/cpython-(3\.[0-9]+)\..*/\1/' <<<"$FT")
[ -n "$FT_MINOR" ] || { echo "no free-threaded CPython in uv's download list"; exit 1; }
uv python install 3.14 "${FT_MINOR}t"
# python / python3 -> the GIL 3.14 (explicit links rather than --default, which has changed across uv releases).
ln -sfn python3.14 "$UV_PYTHON_BIN_DIR/python3"
ln -sfn python3.14 "$UV_PYTHON_BIN_DIR/python"
# CLI tools, on the GIL 3.14 (uv would otherwise pick the newest installed, the free-threaded one). ruff pinned to CI (hyper-test-python.yml); pyflakes so actionlint checks python run: steps.
uv tool install --python 3.14 ruff==0.17.3
uv tool install --python 3.14 mypy
uv tool install --python 3.14 'maturin>=1.15,<2.0'
uv tool install --python 3.14 pyflakes
uv tool install --python 3.14 pytest
uv cache clean
uv --version
python -c 'import sys; print("GIL:", sys.version)'
"python${FT_MINOR}t" -c 'import sys; print("free-threaded:", sys.version, "gil_enabled=", sys._is_gil_enabled())'
