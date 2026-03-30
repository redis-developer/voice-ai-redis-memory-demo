# Terraform EC2 Deploy

This repo can be deployed in the same style as `dealership-chatbot-adk`:

- one EC2 instance
- Docker Compose on the instance
- public ports for frontend, backend, and memory server

## Defaults copied from the other repo

- AWS region: `us-east-2`
- Instance type: `t3.medium`
- Key pair: `bhavana`
- Environment: `dev`

## Requirements

- AWS CLI configured locally
- Terraform installed
- A reachable GitHub repo URL for this project
- A local `.env` file in the repo root with:
  - `SARVAM_API_KEY`
  - `OPENAI_API_KEY`
  - `REDIS_URL`

Optional `.env` values:

- `AWS_REGION`
- `INSTANCE_TYPE`
- `KEY_NAME`
- `APP_NAME`
- `ENVIRONMENT`
- `REPO_URL`
- `REPO_BRANCH`
- `ALLOWED_SSH_CIDR`
- `OLLAMA_URL`
- `OLLAMA_MODEL`

## Important

The EC2 instance deploys by cloning `REPO_URL` and checking out `REPO_BRANCH`.
If you want the server to run your latest local changes, push them to GitHub first.

## Deploy

```bash
chmod +x terraform/deploy.sh
./terraform/deploy.sh
```

## Outputs

Terraform prints:

- `frontend_url`
- `backend_url`
- `memory_server_url`
- `ssh_command`
