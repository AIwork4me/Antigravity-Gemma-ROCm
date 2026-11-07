#!/usr/bin/env bash
# Retry-wrapped model pull: ollama resumes partial layer downloads.
export PATH="$PATH:$LOCALAPPDATA/Programs/Ollama"
cd "$(cd "$(dirname "$0")/.." && pwd)"
for model in gemma4:26b gemma4:12b; do
  for attempt in $(seq 1 12); do
    echo "=== pull $model attempt $attempt $(date -Iseconds) ===" >> artifacts/ollama/pull-retry.log
    if ollama pull "$model" >> artifacts/ollama/pull-retry.log 2>&1; then
      echo "=== PULL_OK $model attempt $attempt $(date -Iseconds) ===" >> artifacts/ollama/pull-retry.log
      break
    fi
    echo "=== pull $model failed attempt $attempt, sleeping 20s ===" >> artifacts/ollama/pull-retry.log
    sleep 20
  done
done
ollama list >> artifacts/ollama/pull-retry.log 2>&1
echo "ALL_PULLS_DONE $(date -Iseconds)" >> artifacts/ollama/pull-retry.log
