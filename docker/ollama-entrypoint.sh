#!/bin/sh
# Ollama entrypoint wrapper: preload the demo models, then serve.
# Model list is configurable via OLLAMA_PRELOAD_MODELS (space separated).
set -e

MODELS="${OLLAMA_PRELOAD_MODELS:-qwen3:4b nomic-embed-text}"

/bin/ollama serve &
OLLAMA_PID=$!

for model in $MODELS; do
    echo "Pulling model: $model"
    /bin/ollama pull "$model" || echo "WARNING: failed to pull $model" >&2
done

wait "$OLLAMA_PID"
