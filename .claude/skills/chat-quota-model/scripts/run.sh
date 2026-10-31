#!/usr/bin/env bash
# Model-check the daily chat quota model with TLC.
# Usage: run.sh [config ...]   (config names from model/expected.tsv; default: all of them)
# Prints one line per config and the directory holding each full TLC output (<config>.out).
# Exit 0 when every result matches model/expected.tsv, 1 when any differs, 2 on setup errors.
set -euo pipefail

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="$SKILL/model"

TLA_VERSION="v1.7.4"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/${TLA_VERSION}/tla2tools.jar"
TLA_SHA256="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
CACHE="${MIRU_TLA_CACHE:-$HOME/.cache/miru-chat-quota-model}/tla2tools-${TLA_VERSION}"
JAR="$CACHE/tla2tools.jar"

win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }

command -v java >/dev/null 2>&1 || { echo "java not found on PATH (TLC needs Java 11+)" >&2; exit 2; }

if [ ! -f "$JAR" ]; then
  mkdir -p "$(dirname "$CACHE")"
  FETCH="$(mktemp -d "$(dirname "$CACHE")/fetch.XXXXXX")"
  echo "fetching tla2tools.jar ${TLA_VERSION} into $FETCH" >&2
  curl -fsSL -o "$FETCH/tla2tools.jar" "$TLA_URL"
  mkdir -p "$CACHE"
  mv "$FETCH/tla2tools.jar" "$JAR"
  rmdir "$FETCH"
fi
GOT_SHA="$(sha256sum "$JAR" | cut -d' ' -f1)"
if [ "$GOT_SHA" != "$TLA_SHA256" ]; then
  echo "tla2tools.jar SHA-256 mismatch: got $GOT_SHA, want $TLA_SHA256 ($JAR). Not running it." >&2
  exit 2
fi

if [ "$#" -gt 0 ]; then CONFIGS=("$@"); else mapfile -t CONFIGS < <(cut -f1 "$MODEL/expected.tsv"); fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/chat-quota-model.XXXXXX")"
cd "$WORK"

status=0
printf '%-24s %-20s %-20s %s\n' CONFIG EXPECTED GOT STATES
for cfg in "${CONFIGS[@]}"; do
  expected="$(awk -F'\t' -v c="$cfg" '$1 == c { print $2 }' "$MODEL/expected.tsv")"
  if [ -z "$expected" ] || [ ! -f "$MODEL/$cfg.cfg" ]; then
    echo "unknown config: $cfg (see $MODEL/expected.tsv)" >&2
    exit 2
  fi
  out="$WORK/$cfg.out"
  java -XX:+UseParallelGC -cp "$(win "$JAR")" tlc2.TLC -workers 1 -cleanup \
    -metadir "$(win "$WORK/meta-$cfg")" -config "$(win "$MODEL/$cfg.cfg")" \
    "$(win "$MODEL/MC.tla")" >"$out" 2>&1 || true
  if grep -q "Model checking completed. No error has been found." "$out"; then
    got=pass
  elif grep -q "^Error: Invariant .* is violated" "$out"; then
    got="$(sed -n 's/^Error: Invariant \(.*\) is violated.*/\1/p' "$out" | head -n1)"
  else
    got=tlc_error
  fi
  states="$(sed -n 's/^\([0-9,]*\) states generated, \([0-9,]*\) distinct states found.*/\2/p' "$out" | tail -n1)"
  mark=""
  if [ "$got" != "$expected" ]; then mark="  <-- differs from expected.tsv"; status=1; fi
  printf '%-24s %-20s %-20s %s%s\n' "$cfg" "$expected" "$got" "${states:-?}" "$mark"
done
echo "TLC output per config: $WORK/<config>.out"
exit "$status"
