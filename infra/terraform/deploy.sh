#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT_DIR/infra/terraform"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing $ROOT_DIR/.env"
  exit 1
fi

for cmd in aws docker terraform git; do
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
: "${GOOGLE_CLIENT_ID:?GOOGLE_CLIENT_ID must be set in .env}"

AWS_REGION="${AWS_REGION:-ap-south-1}"
PROJECT_NAME="${PROJECT_NAME:-voice-journal}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
MEMORY_SERVER_AMD64_DIGEST="${MEMORY_SERVER_AMD64_DIGEST:-sha256:81498ba655de159a249a161b9e3f8ea699fede063b6938b6f45781d25e7cf75e}"
BACKEND_CORS_ORIGINS="${BACKEND_CORS_ORIGINS:-*}"
OLLAMA_URL="${OLLAMA_URL:-}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2}"
MEMORY_SERVER_GENERATION_MODEL="${MEMORY_SERVER_GENERATION_MODEL:-gpt-4o-mini}"

export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_project_name="$PROJECT_NAME"
export TF_VAR_environment="$ENVIRONMENT"
export TF_VAR_image_tag="$IMAGE_TAG"
export TF_VAR_sarvam_api_key="$SARVAM_API_KEY"
export TF_VAR_openai_api_key="$OPENAI_API_KEY"
export TF_VAR_redis_url="$REDIS_URL"
export TF_VAR_google_client_id="$GOOGLE_CLIENT_ID"
export TF_VAR_backend_cors_origins="$BACKEND_CORS_ORIGINS"
export TF_VAR_ollama_url="$OLLAMA_URL"
export TF_VAR_ollama_model="$OLLAMA_MODEL"
export TF_VAR_memory_server_generation_model="$MEMORY_SERVER_GENERATION_MODEL"
export NEXT_PUBLIC_GOOGLE_CLIENT_ID="${NEXT_PUBLIC_GOOGLE_CLIENT_ID:-$GOOGLE_CLIENT_ID}"

pushd "$TF_DIR" >/dev/null

terraform init

terraform apply \
  -auto-approve \
  -target=aws_ecr_repository.frontend \
  -target=aws_ecr_repository.backend \
  -target=aws_ecr_repository.memory_server \
  -target=aws_iam_role.apprunner_ecr_access \
  -target=aws_iam_role_policy_attachment.apprunner_ecr_access

FRONTEND_REPO="$(terraform output -raw frontend_ecr_repository_url)"
BACKEND_REPO="$(terraform output -raw backend_ecr_repository_url)"
MEMORY_REPO="$(terraform output -raw memory_server_ecr_repository_url)"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker buildx build \
  --platform linux/amd64 \
  --push \
  -f "$ROOT_DIR/docker/Dockerfile.backend" \
  -t "${BACKEND_REPO}:${IMAGE_TAG}" \
  "$ROOT_DIR"

docker buildx imagetools create \
  --tag "${MEMORY_REPO}:${IMAGE_TAG}" \
  "docker.io/redislabs/agent-memory-server:latest@${MEMORY_SERVER_AMD64_DIGEST}"

terraform apply \
  -auto-approve \
  -target=aws_apprunner_service.memory_server \
  -target=aws_apprunner_service.backend

BACKEND_URL="$(terraform output -raw backend_url)"

docker buildx build \
  --platform linux/amd64 \
  --push \
  -f "$ROOT_DIR/docker/Dockerfile.frontend" \
  --build-arg "NEXT_PUBLIC_API_URL=${BACKEND_URL}" \
  --build-arg "NEXT_PUBLIC_GOOGLE_CLIENT_ID=${NEXT_PUBLIC_GOOGLE_CLIENT_ID}" \
  -t "${FRONTEND_REPO}:${IMAGE_TAG}" \
  "$ROOT_DIR"

terraform apply -auto-approve

echo
echo "Frontend URL: $(terraform output -raw frontend_url)"
echo "Backend URL: $(terraform output -raw backend_url)"
echo "Memory server URL: $(terraform output -raw memory_server_url)"

popd >/dev/null
