# AWS Deployment Guide

This project can be shared as a hosted web app for a small team by running three public services:

- `frontend`: Next.js web app
- `backend`: FastAPI API
- `memory-server`: Redis Agent Memory Server

For a two-person testing setup, the simplest AWS path is:

- `AWS App Runner` for `frontend`
- `AWS App Runner` for `backend`
- `AWS App Runner` for `memory-server`
- `Amazon ElastiCache for Redis OSS` or your existing Redis provider
- `AWS Secrets Manager` for API keys

If you want lower-latency conversational responses without calling a public LLM API,
run `Ollama` on a small EC2 instance in the same AWS region as the backend and point
the backend at it with `OLLAMA_URL`.

## Architecture

```text
Colleague Browser
  -> Frontend App Runner
  -> Backend App Runner
  -> Memory Server App Runner
  -> Redis
```

Use HTTPS everywhere so browser microphone access works correctly.

For Google login, create a Google OAuth web client and copy its client ID into both:

- `GOOGLE_CLIENT_ID`
- `NEXT_PUBLIC_GOOGLE_CLIENT_ID`

Also add these Authorized JavaScript origins in Google Cloud Console:

- `http://localhost:3000`
- `https://<frontend-service-url>`

## Before You Deploy

1. Rotate any AWS secret that was pasted into chat or another unsafe place.
2. Store app secrets in AWS Secrets Manager instead of source control.
3. Decide whether you want to keep using your current Redis provider or move to AWS Redis later.

## Service 1: Memory Server

Create an App Runner service from the public image:

- Image: `redislabs/agent-memory-server:latest`
- Port: `8000`
- Start command:

```bash
agent-memory api --host 0.0.0.0 --port 8000 --task-backend asyncio
```

Environment variables:

```text
OPENAI_API_KEY=<from Secrets Manager>
REDIS_URL=<your redis url>
GENERATION_MODEL=gpt-4o-mini
```

After deployment, note the service URL:

```text
https://<memory-service-url>
```

## Service 2: Backend

Create an App Runner service from this repository using `docker/Dockerfile.backend`.

- Port: `8080`
- Root directory: repository root
- Dockerfile path: `docker/Dockerfile.backend`

Environment variables:

```text
SARVAM_API_KEY=<from Secrets Manager>
OPENAI_API_KEY=<from Secrets Manager>
REDIS_URL=<your redis url>
GOOGLE_CLIENT_ID=<your google oauth client id>
MEMORY_SERVER_URL=https://<memory-service-url>
CORS_ORIGINS=https://<frontend-service-url>
OLLAMA_URL=
OLLAMA_MODEL=llama3.2
```

Notes:

- Leave `OLLAMA_URL` empty if you are not hosting Ollama.
- If you host Ollama on EC2, use the EC2 private IP here, for example
  `OLLAMA_URL=http://10.0.12.34:11434`.
- Smallest EC2 instance I would trust for `llama3.2`: `t3.large` in the same region
  as the backend. AWS documents `t3.large` with `2 vCPUs` and `8 GiB` memory, which
  is a practical floor for a warm small model in this app.
- If Redis is private inside a VPC, attach an App Runner VPC connector.

## Optional: Ollama on EC2

Recommended shape:

- Region: same as backend, currently `us-east-2`
- Instance type: `t3.large`
- OS: Ubuntu 24.04 LTS
- Model: `llama3.2`

Why:

- the model is small enough for CPU inference
- same-region traffic keeps backend-to-Ollama latency low
- keeping the model warm matters more than shaving a few milliseconds of network time

Bootstrap script:

- [`infra/ollama/install_ubuntu.sh`](/Users/bhavana.giri/DevRepo/voice_ai_redis_memory_demo/infra/ollama/install_ubuntu.sh)

After EC2 is ready:

1. set `OLLAMA_URL=http://<ollama-private-ip>:11434` in `.env`
2. run `./infra/terraform/deploy.sh`
3. verify the backend can reach Ollama from the logs

After deployment, note the backend URL:

```text
https://<backend-service-url>
```

## Service 3: Frontend

Create an App Runner service from this repository using `docker/Dockerfile.frontend`.

- Port: `3000`
- Root directory: repository root
- Dockerfile path: `docker/Dockerfile.frontend`

Build argument:

```text
NEXT_PUBLIC_API_URL=https://<backend-service-url>
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<your google oauth client id>
```

After deployment, note the frontend URL:

```text
https://<frontend-service-url>
```

Then update the backend `CORS_ORIGINS` setting to match that frontend URL exactly.

## Recommended Deploy Order

1. Deploy `memory-server`
2. Deploy `backend`
3. Deploy `frontend`
4. Update backend `CORS_ORIGINS` with the final frontend URL
5. Test recording, chat, and mood save from your browser
6. Share the frontend URL with your colleague

## Test Checklist

- `GET /api/health` returns healthy from the backend
- microphone permission prompt appears in the browser
- voice recording transcribes successfully
- chat responses return with audio
- your colleague sees their own data, not yours

## Cost-Saving Option

If App Runner feels too expensive for three always-on services, run the stack on one EC2 instance with Docker Compose and put HTTPS in front with Caddy or Nginx. That is cheaper, but more hands-on than App Runner.
