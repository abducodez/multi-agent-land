#!/usr/bin/env bash
# Manual curl smoke-tests for every Modal LLM endpoint.
#
# Each block sends one tiny chat completion ("What is the capital of France?")
# and prints the HTTP status, total time, and the response body. Run them ONE AT
# A TIME by copy-pasting a block, or run the whole file: `bash modal/curl_tests.sh`.
# Pass a number to run just that model: `bash modal/curl_tests.sh 3`.
#
# Notes:
#   * -L is REQUIRED: a cold endpoint returns an HTTP 303 redirect at ~150s while
#     the GPU container boots; curl must follow it. Without -L you get a bare 303.
#   * --max-time 900 gives a cold start (weight download + load) room to finish.
#   * `model` is the SERVED id (the HF repo id), NOT the URL slug.
#   * Set LLM_API_KEY if the apps were deployed with auth; otherwise "EMPTY" works.

set -u
WS="${MODAL_WORKSPACE:-gharsallah-abderrahmen}"
KEY="${LLM_API_KEY:-EMPTY}"
PROMPT="What is the capital of France?"
MAXTIME=900

# call <n> <label> <app> <endpoint-slug> <served-model-id>
call() {
  local n="$1" label="$2" app="$3" slug="$4" model="$5"
  local url="https://${WS}--${app}-${slug}.modal.run/v1/chat/completions"
  echo "============================================================"
  echo "[$n] $label"
  echo "    model: $model"
  echo "    url  : $url"
  echo "------------------------------------------------------------"
  curl -sS -L --max-time "$MAXTIME" \
    -X POST "$url" \
    -H "Authorization: Bearer ${KEY}" \
    -H "Content-Type: application/json" \
    -d "{
          \"model\": \"${model}\",
          \"messages\": [{\"role\": \"user\", \"content\": \"${PROMPT}\"}],
          \"max_tokens\": 64,
          \"temperature\": 0
        }" \
    -w $'\n>>> HTTP %{http_code} | %{num_redirects} redirects | %{time_total}s\n'
  echo
}

# ---- one block per model (run individually if you prefer) -------------------
run_1() { call 1 "NVIDIA Nemotron-3-Nano-4B (tiny)"      "nvidia-llms"  "nemotron-3-nano-4b"   "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"; }
# run_2 (nemotron-3-nano-30b) removed — dropped from the catalogue for the 8-fn cap.
run_3() { call 3 "NVIDIA Nemotron-Cascade-14B-Thinking"  "nvidia-llms"  "nemotron-cascade-14b" "nvidia/Nemotron-Cascade-14B-Thinking"; }
run_4() { call 4 "OpenBMB MiniCPM4.1-8B (fast)"          "openbmb-llms" "minicpm-4-1-8b"       "openbmb/MiniCPM4.1-8B"; }
run_5() { call 5 "OpenBMB MiniCPM-o-4.5 (omni)"          "openbmb-llms" "minicpm-o-4-5"        "openbmb/MiniCPM-o-4_5"; }
run_6() { call 6 "Google Gemma-4-12B (balanced)"         "google-llms"  "gemma-4-12b"          "google/gemma-4-12B"; }
run_7() { call 7 "Google Gemma-4-26B (strong, H200)"     "google-llms"  "gemma-4-26b"          "google/gemma-4-26B-A4B-it"; }

# ---- driver: run all, or just the number passed as $1 -----------------------
if [ "$#" -ge 1 ]; then
  "run_$1"
else
  for i in 1 3 4 5 6 7; do "run_$i"; done
fi
