# Server Setup (VPS Deployment)

This guide walks you through deploying the C++ Standard MCP server on a clean Ubuntu VPS. After completing these steps, users will connect with just a URL and an API key.

## Prerequisites

- A VPS with **Ubuntu 22.04 or 24.04 LTS**
- A **domain name** pointed at the server's IP address (e.g. `mcpserver1.cpp.al`)
- **SSH access** to the server

## 1. System setup

SSH into the server and run:

```bash
sudo apt update && sudo apt upgrade -y
```

### Install Python 3.12+

Ubuntu 24.04 ships with Python 3.12. For 22.04:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.12 python3.12-venv
```

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### Install git

```bash
sudo apt install git
```

### Install Caddy

Caddy handles HTTPS automatically via Let's Encrypt.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

### Create a deploy user

```bash
sudo useradd -m -s /bin/bash cppmcp
sudo su - cppmcp
```

## 2. Install cpp-mcp

As the `cppmcp` user:

```bash
cd ~
git clone git@github.com:cppalliance/wg21-paperflow.git
cd cppa-wg21-paperflow
uv venv
uv pip install -e packages/cpp-mcp
```

Verify:

```bash
~/.venv/bin/cpp-mcp --help
```

## 3. Ingest the standard

```bash
export CPP_MCP_DATA_DIR=/home/cppmcp/data
mkdir -p $CPP_MCP_DATA_DIR

~/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n5008
```

To ingest multiple versions:

```bash
~/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n4950
~/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n5008
```

The most recently ingested version becomes the default. Override with `--default-draft` when starting the server.

## 4. Create API keys

```bash
sudo mkdir -p /etc/cpp-mcp
sudo touch /etc/cpp-mcp/keys
sudo chown cppmcp:cppmcp /etc/cpp-mcp/keys
sudo chmod 600 /etc/cpp-mcp/keys
```

Generate keys and add them to the file:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edit `/etc/cpp-mcp/keys`:

```
# One key per line. Blank lines and lines starting with # are ignored.
# Generate a key: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Mungo - issued 2026-05-28
<paste-generated-key-here>

# paperflow pipelines - issued 2026-05-28
<paste-generated-key-here>

# wg21.org website - issued 2026-05-28
<paste-generated-key-here>
```

To add or revoke a key later: edit this file, then reload the service (step 5 below).

## 5. Systemd service

Create `/etc/systemd/system/cpp-mcp.service`:

```ini
[Unit]
Description=C++ Standard MCP Server
After=network.target

[Service]
Type=simple
User=cppmcp
Group=cppmcp
WorkingDirectory=/home/cppmcp
Environment=CPP_MCP_DATA_DIR=/home/cppmcp/data
ExecStart=/home/cppmcp/.venv/bin/cpp-mcp serve --host 127.0.0.1 --port 8001 --keys-file /etc/cpp-mcp/keys
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cpp-mcp
sudo systemctl start cpp-mcp
```

Check it's running:

```bash
sudo systemctl status cpp-mcp
curl http://127.0.0.1:8001/mcp
```

To reload API keys without restarting:

```bash
sudo systemctl reload cpp-mcp
```

## 6. Caddy reverse proxy

Edit `/etc/caddy/Caddyfile`:

```
mcpserver1.cpp.al {
    reverse_proxy 127.0.0.1:8001
}
```

Replace `mcpserver1.cpp.al` with your actual domain. Caddy will automatically obtain and renew a Let's Encrypt certificate (same CA as wg21.org).

Restart Caddy:

```bash
sudo systemctl restart caddy
```

## 7. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

Nothing else needs to be exposed. Port 8001 is localhost-only.

## 8. Verify

From your local machine:

```bash
curl https://mcpserver1.cpp.al/mcp
```

Test with authentication (replace `YOUR_API_KEY`):

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" https://mcpserver1.cpp.al/mcp
```

## 9. Connect from Cursor

Users add this to their `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "cpp-standard": {
      "url": "https://mcpserver1.cpp.al/mcp",
      "headers": {
        "Authorization": "Bearer <api-key>"
      }
    }
  }
}
```

That's all they need. No SSH keys, no tunnels, no client-side setup.

## Managing API keys

Keys live in `/etc/cpp-mcp/keys`. One key per line, `#` comments allowed.

**Add a key:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))" >> /etc/cpp-mcp/keys
sudo systemctl reload cpp-mcp
```

**Revoke a key:** remove the line from the file, then reload:

```bash
sudo systemctl reload cpp-mcp
```

The reload (SIGHUP) re-reads the keys file without dropping existing connections.

## Re-indexing

When a new draft is published:

```bash
sudo -u cppmcp CPP_MCP_DATA_DIR=/home/cppmcp/data \
    /home/cppmcp/.venv/bin/cpp-mcp ingest --tag n5025
sudo systemctl restart cpp-mcp
```

Old versions remain in the database. The new version becomes the default.

## Monitoring

```bash
# Service status
sudo systemctl status cpp-mcp

# Live logs
sudo journalctl -u cpp-mcp -f

# Recent logs
sudo journalctl -u cpp-mcp --since "1 hour ago"

# Caddy logs
sudo journalctl -u caddy -f
```

## Backups

The `.db` file is the only state:

```bash
cp /home/cppmcp/data/standard.db /home/cppmcp/data/standard.db.bak
```

The database can always be regenerated from scratch by re-running `ingest`.

## Setting a default draft

If you want a specific version to be the default (instead of the most recently ingested):

Edit the systemd unit to add `--default-draft`:

```ini
ExecStart=/home/cppmcp/.venv/bin/cpp-mcp serve --host 127.0.0.1 --port 8001 --keys-file /etc/cpp-mcp/keys --default-draft n5008
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart cpp-mcp
```
