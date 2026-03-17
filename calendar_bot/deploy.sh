#!/bin/bash
# Deploy Calendar Bot to VPS via SSH
# Usage: ./deploy.sh <server_ip> <server_user> <server_password>
set -e

SERVER_IP="${1:?Usage: ./deploy.sh <server_ip> <server_user>}"
SERVER_USER="${2:-root}"

REMOTE_DIR="/opt/calendar-bot"

echo "=== Deploying Calendar Bot to $SERVER_USER@$SERVER_IP ==="

# Create remote directory
ssh "$SERVER_USER@$SERVER_IP" "mkdir -p $REMOTE_DIR"

# Copy bot files
scp -r ../calendar_bot/*.py "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"
scp ../calendar_bot/requirements.txt "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"
scp ../calendar_bot/.env "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/.env" 2>/dev/null || echo "No .env file found, will need to create on server"

# Install dependencies and setup on server
ssh "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
set -e
cd /opt/calendar-bot

# Install Python if needed
if ! command -v python3 &> /dev/null; then
    apt-get update && apt-get install -y python3 python3-pip python3-venv
fi

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service
cat > /etc/systemd/system/calendar-bot.service << 'EOF'
[Unit]
Description=Telegram Calendar Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/calendar-bot
EnvironmentFile=/opt/calendar-bot/.env
ExecStart=/opt/calendar-bot/venv/bin/python -m calendar_bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload and restart
systemctl daemon-reload
systemctl enable calendar-bot
systemctl restart calendar-bot

echo "=== Bot deployed and started! ==="
echo "Check status: systemctl status calendar-bot"
echo "View logs: journalctl -u calendar-bot -f"
ENDSSH

echo "=== Deployment complete! ==="
