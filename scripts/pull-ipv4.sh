#!/usr/bin/env bash
# pull-ipv4.sh — fetch an Ollama model via the official registry protocol over IPv4.
# Equivalent to `ollama pull` but forces curl -4 to dodge broken local IPv6 routes
# to Cloudflare R2. Verifies every blob's sha256 before installing into the
# Ollama content store (~/.ollama/models), then lets `ollama list` see it.
set -uo pipefail
REG="https://registry.ollama.ai"
REPO="${1:-library/gemma4}"
TAG="${2:-26b}"
MODELS_DIR="${OLLAMA_MODELS:-$HOME/.ollama/models}"
BLOBS="$MODELS_DIR/blobs"
WORK=""$(cd "$(dirname "$0")/.." && pwd)/artifacts"/ollama/work"
PY=""$(cd "$(dirname "$0")/.." && pwd)/.venv/Scripts/python.exe""
LOG=""$(cd "$(dirname "$0")/.." && pwd)/artifacts"/ollama/pull-ipv4.log"
ACCEPT="application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json"

mkdir -p "$BLOBS" "$WORK" "$MODELS_DIR/manifests/registry.ollama.ai/$(dirname "$REPO")"
M="$WORK/manifest-$TAG.json"
B="$WORK/blobs-$TAG.txt"
echo "=== pull-ipv4 $REPO:$TAG start $(date -Iseconds) ===" >> "$LOG"

curl -4 -sS --retry 5 --retry-delay 5 --max-time 120 -H "Accept: $ACCEPT" \
  "$REG/v2/$REPO/manifests/$TAG" -o "$M" || { echo "MANIFEST_FETCH_FAILED" >> "$LOG"; exit 1; }

# If it's a manifest list, descend to the linux/amd64 entry
IS_LIST=$("$PY" -c "import json,sys; m=json.load(open(sys.argv[1],encoding='utf-8')); print('yes' if 'manifests' in m else 'no')" "$(cygpath -w "$M")" 2>/dev/null || echo no)
if [ "$IS_LIST" = "yes" ]; then
  "$PY" - "$(cygpath -w "$M")" <<'PYEOF' > "$WORK/digest.txt"
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
for e in m['manifests']:
    p = e.get('platform', {})
    if p.get('os') == 'linux' and p.get('architecture') == 'amd64':
        print(e['digest']); break
PYEOF
  DIGEST=$(cat "$WORK/digest.txt")
  echo "manifest list -> amd64 $DIGEST" >> "$LOG"
  curl -4 -sS --retry 5 --retry-delay 5 --max-time 120 -H "Accept: $ACCEPT" \
    "$REG/v2/$REPO/manifests/$DIGEST" -o "$M"
fi

"$PY" - "$(cygpath -w "$M")" <<'PYEOF' > "$B"
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
print(m['config']['digest'], m['config']['size'])
for l in m['layers']:
    print(l['digest'], l['size'])
PYEOF
NLINES=$(wc -l < "$B")
if [ "$NLINES" -lt 1 ] || [ ! -s "$B" ]; then
  echo "MANIFEST_PARSE_FAILED (blobs list empty)" >> "$LOG"; exit 1
fi
echo "layers to fetch ($NLINES):" >> "$LOG"; cat "$B" >> "$LOG"

FAIL=0
while read -r DIGEST SIZE; do
  [ -z "$DIGEST" ] && continue
  DEST="$BLOBS/${DIGEST/:/-}"
  if [ -f "$DEST" ]; then
    ACT=$(stat -c %s "$DEST" 2>/dev/null || echo 0)
    H=$(sha256sum "$DEST" | cut -d' ' -f1)
    if [ "$ACT" = "$SIZE" ] && [ "sha256:$H" = "$DIGEST" ]; then
      echo "SKIP verified $DIGEST" >> "$LOG"; continue
    fi
    echo "INCOMPLETE/CORRUPT $DIGEST have=$ACT — restarting it" >> "$LOG"; rm -f "$DEST"
  fi
  echo "FETCH $DIGEST ($SIZE bytes) start $(date -Iseconds)" >> "$LOG"
  for attempt in $(seq 1 10); do
    if curl -4 -L -C - --retry 3 --retry-delay 5 --connect-timeout 20 --speed-time 90 --speed-limit 10240 \
         "$REG/v2/$REPO/blobs/$DIGEST" -o "$DEST" >> "$LOG" 2>&1; then
      ACT=$(stat -c %s "$DEST" 2>/dev/null || echo 0)
      [ "$ACT" = "$SIZE" ] && break
      echo "size mismatch have=$ACT want=$SIZE attempt=$attempt" >> "$LOG"
    else
      echo "curl attempt=$attempt failed" >> "$LOG"
    fi
    sleep 10
  done
  H=$(sha256sum "$DEST" 2>/dev/null | cut -d' ' -f1)
  if [ "sha256:$H" = "$DIGEST" ]; then
    echo "VERIFIED $DIGEST $(date -Iseconds)" >> "$LOG"
  else
    echo "VERIFY_FAILED $DIGEST" >> "$LOG"; FAIL=1
  fi
done < "$B"

if [ "$FAIL" = "0" ]; then
  cp "$M" "$MODELS_DIR/manifests/registry.ollama.ai/$REPO/$TAG"
  echo "MANIFEST_INSTALLED $REPO:$TAG" >> "$LOG"
  echo "=== PULL_IPV4_OK $REPO:$TAG $(date -Iseconds) ===" >> "$LOG"
  exit 0
else
  echo "=== PULL_IPV4_FAILED ===" >> "$LOG"
  exit 1
fi
