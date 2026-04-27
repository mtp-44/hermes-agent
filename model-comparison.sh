#!/usr/bin/env bash
# Model head-to-head comparison: qwen3.6:35b-a3b vs qwen3.5:35b-a3b-nvfp4
# Tests: cold-start latency, throughput, exact-format obedience, reasoning/tool prompt

set -euo pipefail

OLLAMA_URL="http://127.0.0.1:11434"
MODELS=("qwen3.6:35b-a3b" "qwen3.5:35b-a3b-nvfp4")
LOG_DIR="$(dirname "$0")/comparison-results"
mkdir -p "$LOG_DIR"

unload_all() {
  # Set keep_alive=0 on both models to force unload
  for m in "${MODELS[@]}"; do
    curl -s -X POST "$OLLAMA_URL/api/generate" \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"$m\",\"prompt\":\"\",\"keep_alive\":0}" > /dev/null 2>&1 || true
  done
  sleep 2
}

run_generate() {
  local model="$1"
  local prompt="$2"
  local num_predict="${3:-200}"
  local think="${4:-false}"

  local payload
  payload=$(jq -n \
    --arg model "$model" \
    --arg prompt "$prompt" \
    --argjson num_predict "$num_predict" \
    '{
      model: $model,
      prompt: $prompt,
      stream: false,
      think: false,
      options: {
        num_predict: $num_predict,
        temperature: 0.0
      }
    }')

  curl -s -X POST "$OLLAMA_URL/api/generate" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

print_stats() {
  local label="$1"
  local json="$2"
  local response
  response=$(echo "$json" | jq -r '.response // ""')
  local load_ns prompt_ns eval_ns eval_count prompt_count
  load_ns=$(echo "$json" | jq -r '.load_duration // 0')
  prompt_ns=$(echo "$json" | jq -r '.prompt_eval_duration // 0')
  eval_ns=$(echo "$json" | jq -r '.eval_duration // 0')
  eval_count=$(echo "$json" | jq -r '.eval_count // 0')
  prompt_count=$(echo "$json" | jq -r '.prompt_eval_count // 0')

  local load_ms prompt_ms eval_ms tps
  load_ms=$(echo "scale=0; $load_ns / 1000000" | bc)
  prompt_ms=$(echo "scale=0; $prompt_ns / 1000000" | bc)
  eval_ms=$(echo "scale=0; $eval_ns / 1000000" | bc)
  if [ "$eval_ns" -gt 0 ]; then
    tps=$(echo "scale=1; $eval_count * 1000000000 / $eval_ns" | bc)
  else
    tps=0
  fi

  echo "  load_duration:   ${load_ms}ms"
  echo "  prompt_tokens:   ${prompt_count} tokens in ${prompt_ms}ms"
  echo "  eval_tokens:     ${eval_count} tokens in ${eval_ms}ms (${tps} tok/s)"
  echo "  response:        $(echo "$response" | head -5)"
}

echo "======================================================"
echo " Model Comparison: $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================"
echo ""

# ── TEST 1: Cold-start latency ──────────────────────────────────────────────
echo "TEST 1: Cold-start latency"
echo "  Prompt: 'Reply with exactly the word: READY'"
echo ""

for model in "${MODELS[@]}"; do
  echo "  Unloading all models..."
  unload_all
  sleep 3
  echo "[$model]"
  result=$(run_generate "$model" "Reply with exactly the word: READY" 10)
  echo "$result" > "$LOG_DIR/cold_start_${model//[:\/]/_}.json"
  print_stats "cold-start" "$result"
  echo ""
done

# ── TEST 2: Throughput (sustained generation) ────────────────────────────────
echo "TEST 2: Throughput — 300 token generation (warm model)"
echo "  Prompt: 'Write a detailed explanation of how mixture-of-experts models work, covering routing, expert selection, and load balancing. Be thorough.'"
echo ""

for model in "${MODELS[@]}"; do
  echo "[$model]"
  result=$(run_generate "$model" "Write a detailed explanation of how mixture-of-experts models work, covering routing, expert selection, and load balancing. Be thorough." 300)
  echo "$result" > "$LOG_DIR/throughput_${model//[:\/]/_}.json"
  print_stats "throughput" "$result"
  echo ""
done

# ── TEST 3: Exact-format obedience ──────────────────────────────────────────
echo "TEST 3: Exact-format obedience"
echo ""

EXACT_PROMPTS=(
  "Reply with exactly and only: OBEY"
  "Output a JSON object with one key 'status' and value 'ok'. Output nothing else."
  "Count to 5, one number per line, nothing else."
)

for model in "${MODELS[@]}"; do
  echo "[$model]"
  for prompt in "${EXACT_PROMPTS[@]}"; do
    result=$(run_generate "$model" "$prompt" 50)
    response=$(echo "$result" | jq -r '.response // ""')
    echo "  Prompt: $prompt"
    echo "  → $(echo "$response" | tr '\n' '|' | head -c 120)"
    echo ""
  done
  echo ""
done

# ── TEST 4: Reasoning / tool-style prompt ────────────────────────────────────
echo "TEST 4: Hermes-relevant reasoning (tool selection)"
REASONING_PROMPT='You are an AI assistant with access to these tools:
- search_thoughts(query: str) -> list of matching thoughts from the user knowledge base
- search_contacts(query: str) -> list of matching contacts
- search_finance_records(date_from: str, date_to: str) -> list of finance records

A user asks: "Did I note anything about the retrospective from last sprint? Also, what did I spend money on last week?"

Respond with a JSON array of the tool calls you would make, in order. Use the format:
[{"tool": "<name>", "args": {<key>: <value>}}]
Output only the JSON array, nothing else.'

echo ""
for model in "${MODELS[@]}"; do
  echo "[$model]"
  result=$(run_generate "$model" "$REASONING_PROMPT" 200)
  echo "$result" > "$LOG_DIR/reasoning_${model//[:\/]/_}.json"
  response=$(echo "$result" | jq -r '.response // ""')
  eval_count=$(echo "$result" | jq -r '.eval_count // 0')
  eval_ns=$(echo "$result" | jq -r '.eval_duration // 0')
  if [ "$eval_ns" -gt 0 ]; then
    tps=$(echo "scale=1; $eval_count * 1000000000 / $eval_ns" | bc)
  else
    tps=0
  fi
  echo "  tok/s: $tps"
  echo "  response:"
  echo "$response" | sed 's/^/    /'
  echo ""
done

echo "======================================================"
echo " Comparison complete. Raw JSON saved to: $LOG_DIR"
echo "======================================================"
