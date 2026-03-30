#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing $ROOT_DIR/.env"
  exit 1
fi

for cmd in aws terraform git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command not found: $cmd"
    exit 1
  fi
done

set -a
source "$ROOT_DIR/.env"
set +a

: "${SARVAM_API_KEY:?SARVAM_API_KEY must be set in .env}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set in .env}"
: "${REDIS_URL:?REDIS_URL must be set in .env}"

AWS_REGION="${AWS_REGION:-us-east-2}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"
KEY_NAME="${KEY_NAME:-bhavana}"
APP_NAME="${APP_NAME:-voice-journal}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
REPO_URL="${REPO_URL:-https://github.com/bhavana-giri/voice_ai_redis_memory_demo.git}"
REPO_BRANCH="${REPO_BRANCH:-$(git -C "$ROOT_DIR" branch --show-current)}"
ALLOWED_SSH_CIDR="${ALLOWED_SSH_CIDR:-0.0.0.0/0}"
OLLAMA_URL="${OLLAMA_URL:-}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2}"

export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_instance_type="$INSTANCE_TYPE"
export TF_VAR_key_name="$KEY_NAME"
export TF_VAR_app_name="$APP_NAME"
export TF_VAR_environment="$ENVIRONMENT"
export TF_VAR_repo_url="$REPO_URL"
export TF_VAR_repo_branch="$REPO_BRANCH"
export TF_VAR_sarvam_api_key="$SARVAM_API_KEY"
export TF_VAR_redis_url="$REDIS_URL"
export TF_VAR_openai_api_key="$OPENAI_API_KEY"
export TF_VAR_allowed_ssh_cidr="$ALLOWED_SSH_CIDR"
export TF_VAR_ollama_url="$OLLAMA_URL"
export TF_VAR_ollama_model="$OLLAMA_MODEL"

pushd "$TF_DIR" >/dev/null
terraform init
terraform apply -auto-approve
terraform output
popd >/dev/null
