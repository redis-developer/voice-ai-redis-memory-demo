# Terraform Deploy

This Terraform setup provisions:

- 3 ECR repositories
- 1 IAM role for App Runner to pull from ECR
- 3 App Runner services:
  - `memory-server`
  - `backend`
  - `frontend`

## What You Need

- `aws`, `docker`, `terraform`, and `git` installed locally
- AWS credentials already configured in your shell or via `aws configure`
- a local `.env` file in the repo root with:
  - `SARVAM_API_KEY`
  - `OPENAI_API_KEY`
  - `REDIS_URL`
  - `GOOGLE_CLIENT_ID`

Optional `.env` values:

- `AWS_REGION`
- `PROJECT_NAME`
- `ENVIRONMENT`
- `IMAGE_TAG`
- `BACKEND_CORS_ORIGINS`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
- `MEMORY_SERVER_GENERATION_MODEL`
- `NEXT_PUBLIC_GOOGLE_CLIENT_ID`

## Ollama on EC2

For low-latency conversational responses, run Ollama on a small EC2 instance in the
same AWS region as the backend and set:

- `OLLAMA_URL=http://<ollama-private-ip>:11434`
- `OLLAMA_MODEL=llama3.2`

Smallest instance I would trust for `llama3.2` in this setup:

- `t3.large` in `us-east-2`

Why this size:

- AWS documents `t3.large` with `2 vCPUs` and `8 GiB` memory, which is a practical
  minimum for keeping a small `llama3.2` model warm without constant memory pressure.
- Ollama keeps models loaded in memory for faster subsequent requests and supports
  `keep_alive` to keep them hot.

Use [`infra/ollama/install_ubuntu.sh`](/Users/bhavana.giri/DevRepo/voice_ai_redis_memory_demo/infra/ollama/install_ubuntu.sh)
on an Ubuntu EC2 instance to install Ollama, pull `llama3.2`, and preload it.

## Deploy

From the repo root:

```bash
chmod +x infra/terraform/deploy.sh
./infra/terraform/deploy.sh
```

The script does this in order:

1. creates ECR repositories and the App Runner ECR access role
2. builds and pushes the backend image
3. mirrors the Redis Agent Memory Server image into your ECR
4. creates the memory server and backend App Runner services
5. builds the frontend with the live backend URL baked into `NEXT_PUBLIC_API_URL`
6. pushes the frontend image and creates the frontend App Runner service

## Notes

- The backend defaults `CORS_ORIGINS` to `*` for lightweight testing.
- The frontend image must be built after the backend exists because Next.js reads `NEXT_PUBLIC_API_URL` at build time.
- Google login uses Google Identity Services in the frontend and verifies the returned ID token in the backend. The backend maps each Google account to `google_<sub>` so two users stay separate.
- Make sure your Google OAuth client has Authorized JavaScript origins for `http://localhost:3000` and the final App Runner frontend URL.
- This setup passes app secrets into App Runner as environment variables via Terraform. That is quick for testing, but not the best long-term secret management approach.
