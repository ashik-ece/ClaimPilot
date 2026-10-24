#!/bin/zsh
# Local evaluation gate: replays recorded responses on arm64 and applies eval/thresholds.json.
set -e -o pipefail
cd "$(git rev-parse --show-toplevel)"
find eval/results -name "ci_*.json" -delete
run_in_image() {
  docker compose run --rm --no-deps -v "$PWD/eval:/eval" -v "$PWD/backend/llm_cache:/cache:ro" -v "$PWD/backend/app:/srv/app:ro" \
    -e LLM_MODE=replay -e LLM_CACHE_DIR=/cache -e GOOGLE_API_KEY= \
    worker uv run --no-dev python -W ignore -m app.pipeline.evalcli "$@"
}
run_in_image extract --split dev --out /eval/results/ci_preds_extract.json | grep -E "FAILED|wrote" 
run_in_image analyze --extractions /eval/results/ci_preds_extract.json --out /eval/results/ci_preds_pipeline.json | grep -E "wrote|unavailable|INCOMPLETE"
(cd eval && uv run python -m harness.run --predictions results/ci_preds_pipeline.json --split dev --name ci_pipeline_dev >/dev/null && uv run python -m harness.gate results/ci_pipeline_dev.json thresholds.json)
