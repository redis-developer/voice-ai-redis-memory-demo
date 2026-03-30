#!/usr/bin/env bash

set -euo pipefail

MODEL="${OLLAMA_MODEL:-llama3.2}"

if ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl
fi

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

sudo systemctl enable ollama
sudo systemctl restart ollama

# Allow inbound traffic only from your backend security boundary in AWS.
# If you leave this public, lock it down with security groups.
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<'EOF' | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama

until curl -sf http://127.0.0.1:11434/api/tags >/dev/null; do
  sleep 2
done

ollama pull "$MODEL"

# Preload the model so first real requests are faster.
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\":\"${MODEL}\",\"prompt\":\"\",\"keep_alive\":-1}" >/dev/null

echo "Ollama is ready on http://$(hostname -I | awk '{print $1}'):11434 with model ${MODEL}"
