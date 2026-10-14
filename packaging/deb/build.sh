#!/usr/bin/env bash
# Builds dist/void-claude_<version>_all.deb from this checkout: meson's system-wide install plus a control file.
#   bash packaging/deb/build.sh [output folder]
set -euo pipefail
cd "$(dirname "$0")/../.."
VER="$(tr -d '[:space:]' < app/VERSION)"
OUT="$(mkdir -p "${1:-dist}" && cd "${1:-dist}" && pwd)"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT" build-deb' EXIT
meson setup build-deb --prefix=/usr >/dev/null
meson install -C build-deb --destdir "$ROOT" >/dev/null
install -Dm644 LICENSE "$ROOT/usr/share/doc/void-claude/copyright"
mkdir -p "$ROOT/DEBIAN"
sed "s/@VERSION@/$VER/" packaging/deb/control > "$ROOT/DEBIAN/control"
install -m755 packaging/deb/postinst "$ROOT/DEBIAN/postinst"
dpkg-deb --build --root-owner-group "$ROOT" "$OUT/void-claude_${VER}_all.deb"
