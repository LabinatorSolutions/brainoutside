#!/usr/bin/env bash
# Rebuild static/css/tw.css (the compiled Tailwind build that base.html
# loads) from the source static/css/app.css + tailwind.config.js.
#
# Uses the Tailwind v3 STANDALONE CLI — no Node/npm needed. The binary is
# downloaded once into .cache/ (gitignored). Run this after editing
# templates or app.css and COMMIT the rebuilt tw.css: the Docker image
# ships the committed build and has no CSS build step.
#
#   bash scripts/build-css.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=v3.4.17
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ASSET=tailwindcss-windows-x64.exe ;;
  Darwin) ASSET=tailwindcss-macos-$([ "$(uname -m)" = arm64 ] && echo arm64 || echo x64) ;;
  *) ASSET=tailwindcss-linux-$([ "$(uname -m)" = aarch64 ] && echo arm64 || echo x64) ;;
esac

BIN=".cache/tailwindcss-${VERSION}-${ASSET}"
if [ ! -x "$BIN" ]; then
  mkdir -p .cache
  echo "downloading tailwindcss ${VERSION} (${ASSET})..."
  curl -sfL -o "$BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${VERSION}/${ASSET}"
  chmod +x "$BIN"
fi

"$BIN" -c tailwind.config.js -i static/css/app.css -o static/css/tw.css --minify
echo "built static/css/tw.css ($(wc -c < static/css/tw.css) bytes)"
