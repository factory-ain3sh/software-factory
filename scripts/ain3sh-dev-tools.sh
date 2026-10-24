#!/usr/bin/env bash
# Developer tools for ain3sh-dev computers. The environment's global setup
# script runs this after the repositories are cloned and before their setup
# scripts, so factory-mono installs under the Node and npm it pins.
set -euo pipefail

NVM_VERSION=v0.40.8
TUISTORY_VERSION=0.12.0
ASCIINEMA_VERSION=v3.2.1
AGG_VERSION=v1.9.0
MONO="$HOME/repos/factory-mono"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] || curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh" | PROFILE="$HOME/.bashrc" bash
set +u
. "$NVM_DIR/nvm.sh"
nvm install "$(cat "$MONO/.nvmrc")"
nvm alias default "$(cat "$MONO/.nvmrc")"
node_bin="$(dirname "$(nvm which default)")"
set -u
npm install --global "$(node -p "require('$MONO/package.json').packageManager")"
# Tool calls and later build steps run non-interactive shells that never source
# nvm.sh, so the pinned toolchain also goes first on the system PATH.
for tool in node npm npx corepack; do
  sudo ln -sfn "$node_bin/$tool" "/usr/local/bin/$tool"
done
test "$(node --version)" = "v$(cat "$MONO/.nvmrc")"

npm install --global --prefix "$HOME/.local" "tuistory@$TUISTORY_VERSION"
sudo ln -sfn "$HOME/.local/bin/tuistory" /usr/local/bin/tuistory
for release in "asciinema/asciinema/$ASCIINEMA_VERSION/asciinema" "asciinema/agg/$AGG_VERSION/agg"; do
  IFS=/ read -r owner repo version tool <<<"$release"
  sudo curl -fsSL -o "/usr/local/bin/$tool" "https://github.com/$owner/$repo/releases/download/$version/$tool-x86_64-unknown-linux-gnu"
  sudo chmod 0755 "/usr/local/bin/$tool"
done
tuistory --version
asciinema --version
agg --version
