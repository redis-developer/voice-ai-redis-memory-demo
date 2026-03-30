#!/bin/bash
set -e

exec > >(tee /var/log/user-data.log) 2>&1
echo "Starting user data script at $(date)"

apt-get update -y
apt-get upgrade -y

apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git

if ! command -v docker &> /dev/null; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu
fi

if ! command -v docker-compose &> /dev/null && docker compose version &> /dev/null; then
    cat > /usr/local/bin/docker-compose <<'EOF'
#!/bin/bash
docker compose "$@"
EOF
    chmod +x /usr/local/bin/docker-compose
fi

APP_DIR="/opt/${app_name}"
mkdir -p $APP_DIR
cd $APP_DIR

git clone ${repo_url} .
git checkout ${repo_branch}

PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)

cat > $APP_DIR/.env <<EOF
SARVAM_API_KEY=${sarvam_api_key}
REDIS_URL=${redis_url}
MEMORY_SERVER_URL=http://memory-server:8000
OPENAI_API_KEY=${openai_api_key}
NEXT_PUBLIC_API_URL=http://$PUBLIC_IP:8080
CORS_ORIGINS=http://$PUBLIC_IP:3000,http://localhost:3000
OLLAMA_URL=${ollama_url}
OLLAMA_MODEL=${ollama_model}
MEMORY_SERVER_GENERATION_MODEL=gpt-4o-mini
EOF

chown -R ubuntu:ubuntu $APP_DIR

cd $APP_DIR
docker compose down --remove-orphans || true
docker compose build
docker compose up -d

cat > /etc/systemd/system/${app_name}.service <<SERVICE_EOF
[Unit]
Description=Voice Journal Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/${app_name}
ExecStart=/bin/bash -c 'cd /opt/${app_name} && docker compose up -d'
ExecStop=/bin/bash -c 'cd /opt/${app_name} && docker compose down'
User=root

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable ${app_name}.service

echo "Deployment completed at $(date)"
echo "Public IP: $PUBLIC_IP"
echo "Frontend: http://$PUBLIC_IP:3000"
echo "Backend: http://$PUBLIC_IP:8080"
echo "Memory Server: http://$PUBLIC_IP:8000"
