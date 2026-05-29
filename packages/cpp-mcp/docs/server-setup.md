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
cd wg21-paperflow
uv venv
uv pip install -e packages/cpp-mcp
```

Verify:

```bash
~/wg21-paperflow/.venv/bin/cpp-mcp --help
```

## Draft tags

The C++ standard source at [cplusplus/draft](https://github.com/cplusplus/draft) uses numbered tags for each published working draft. The key tags:

| Standard | Tag | Date |
|----------|-----|------|
| C++26 (latest working draft) | `n5046` | 2026-05-12 |
| C++23 (final working draft) | `n4950` | 2023-05-10 |
| C++20 (final working draft) | `n4861` | 2020-04-01 |
| C++17 (final working draft) | `n4659` | 2017-03-21 |
| C++14 (final working draft) | `n4140` | 2014-10-07 |
| C++11 (first post-publication draft) | `n3337` | 2012-01-16 |

Use `main` for the bleeding-edge HEAD of the draft (may contain incomplete edits).

## 3. Ingest the standard

```bash
export CPP_MCP_DATA_DIR=/home/cppmcp/data
mkdir -p $CPP_MCP_DATA_DIR

~/wg21-paperflow/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n5046
```

To ingest multiple versions:

```bash
~/wg21-paperflow/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n4950
~/wg21-paperflow/.venv/bin/cpp-mcp --data-dir $CPP_MCP_DATA_DIR ingest --tag n5046
```

The default is always the highest-numbered tag (most recently published standard), regardless of ingestion order. Override with `--default-draft` when starting the server.

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

Create `/etc/systemd/system/cpp-mcp.service`.

Authentication is required on a server. The `--keys-file` flag points to the keys file created in step 4. Without it (or without `--no-auth`), the server will refuse to start. Never use `--no-auth` on a public-facing server.

If you have a domain name and will use Caddy for HTTPS (section 7), bind to localhost:

```ini
ExecStart=/home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp serve --host 127.0.0.1 --port 8001 --keys-file /etc/cpp-mcp/keys
```

If you do **not** have a domain name yet, bind to all interfaces so clients can reach the server directly over HTTP:

```ini
ExecStart=/home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp serve --host 0.0.0.0 --port 8001 --keys-file /etc/cpp-mcp/keys
```

Full unit file:

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
ExecStart=/home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp serve --host 0.0.0.0 --port 8001 --keys-file /etc/cpp-mcp/keys
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

## 6. Firewall

Install ufw if not already present:

```bash
sudo apt install ufw
```

### Without a domain (HTTP only)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8001/tcp
sudo ufw enable
```

### Google Cloud: VPC firewall

If the server runs on Google Cloud (GCE), the VPC firewall is separate from ufw and blocks ports by default. You must also allow the port at the GCP level:

```bash
# For HTTP-only (port 8001 direct access)
gcloud compute firewall-rules create allow-mcp-8001 \
    --allow tcp:8001 \
    --source-ranges 0.0.0.0/0 \
    --description "Allow MCP server access"

# For HTTPS via Caddy (ports 80 and 443)
gcloud compute firewall-rules create allow-mcp-https \
    --allow tcp:80,tcp:443 \
    --source-ranges 0.0.0.0/0 \
    --description "Allow HTTP/HTTPS for Caddy"
```

Alternatively, create these rules in the GCP Console under **VPC network > Firewall**. These rules persist across reboots and VM stops/starts.

### With a domain (HTTPS via Caddy)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

Port 8001 does not need to be exposed when Caddy is proxying -- it stays on localhost.

## 7. Caddy reverse proxy (requires a domain name)

Skip this section if you do not have a domain name. The server works fine over plain HTTP without Caddy. Come back and add Caddy when DNS is ready.

### DNS prerequisites

Before Caddy can obtain a Let's Encrypt certificate, the domain must resolve to the server's IP address. You need a DNS **A record** (IPv4) and optionally an **AAAA record** (IPv6):

| Type | Name | Value |
|------|------|-------|
| A | `mcpserver1.cpp.al` | `<server-ipv4-address>` |
| AAAA | `mcpserver1.cpp.al` | `<server-ipv6-address>` (optional) |

Set these in your DNS provider's control panel (whoever manages `cpp.al`). The record typically propagates within a few minutes but can take up to an hour.

Verify the record is live before proceeding:

```bash
dig +short mcpserver1.cpp.al
# Should return the server's IP address
```

If `dig` is not installed: `sudo apt install dnsutils`.

### Firewall

Caddy needs ports 80 and 443 open. Port 80 is required even though you only serve HTTPS -- Let's Encrypt uses it for the HTTP-01 challenge during certificate issuance and renewal.

```bash
sudo ufw allow 80
sudo ufw allow 443
```

### Caddyfile

Install Caddy if not already installed (see section 1). Edit `/etc/caddy/Caddyfile`:

```
mcpserver1.cpp.al {
    reverse_proxy 127.0.0.1:8001
}
```

Replace `mcpserver1.cpp.al` with your actual domain. Caddy will automatically obtain and renew a Let's Encrypt certificate. Certificate renewal happens in the background with no downtime.

Restart Caddy:

```bash
sudo systemctl restart caddy
```

Then update the systemd unit to bind to localhost instead of all interfaces:

```bash
# Edit /etc/systemd/system/cpp-mcp.service
# Change --host 0.0.0.0 to --host 127.0.0.1
sudo systemctl daemon-reload
sudo systemctl restart cpp-mcp
```

And update the firewall to close port 8001 and open 80/443:

```bash
sudo ufw delete allow 8001/tcp
sudo ufw allow 80
sudo ufw allow 443
```

## 8. Verify

### Without a domain (HTTP)

From your local machine (replace `<server-ip>` with the server's IP address):

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" http://<server-ip>:8001/mcp
```

### With a domain (HTTPS)

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" https://mcpserver1.cpp.al/mcp
```

## 9. Connect from Cursor

Users add this to their `.cursor/mcp.json`:

### Without a domain (HTTP)

```json
{
  "mcpServers": {
    "cpp-standard": {
      "url": "http://<server-ip>:8001/mcp",
      "headers": {
        "Authorization": "Bearer <api-key>"
      }
    }
  }
}
```

### With a domain (HTTPS)

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

Cursor accepts both HTTP and HTTPS for MCP server URLs. No SSH keys, no tunnels, no client-side setup beyond this config.

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

Ingestion is atomic -- the server continues serving the old data while new data is being parsed. No restart is needed after ingestion.

When a new draft is published:

```bash
sudo -u cppmcp CPP_MCP_DATA_DIR=/home/cppmcp/data \
    /home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp ingest --tag n5025
```

The server picks up the new data automatically via SQLite WAL mode. Old versions remain in the database. The default is always the highest-numbered tag.

### Ingesting the trunk

To ingest the bleeding-edge `main` branch:

```bash
sudo -u cppmcp CPP_MCP_DATA_DIR=/home/cppmcp/data \
    /home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp ingest --tag main
```

Re-ingesting `main` is safe and idempotent -- if the git SHA has not changed since the last ingest, the command skips immediately.

### Daily trunk re-ingestion (optional)

To keep `main` automatically up to date, set up a systemd timer.

Create `/etc/systemd/system/cpp-mcp-ingest-trunk.timer`:

```ini
[Unit]
Description=Daily re-ingestion of C++ standard trunk

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Create `/etc/systemd/system/cpp-mcp-ingest-trunk.service`:

```ini
[Unit]
Description=Re-ingest C++ standard trunk
After=network.target

[Service]
Type=oneshot
User=cppmcp
Environment=CPP_MCP_DATA_DIR=/home/cppmcp/data
ExecStart=/home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp ingest --tag main
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cpp-mcp-ingest-trunk.timer
sudo systemctl start cpp-mcp-ingest-trunk.timer
```

## Upgrades

### Code-only changes (no schema change)

```bash
cd /home/cppmcp/wg21-paperflow
git pull --ff-only
uv pip install -e packages/cpp-mcp
sudo systemctl restart cpp-mcp
```

No re-ingestion needed.

### Schema or parser changes

If the update adds new database tables, columns, or changes how LaTeX is parsed:

```bash
cd /home/cppmcp/wg21-paperflow
git pull --ff-only
uv pip install -e packages/cpp-mcp

# Re-ingest all drafts
CPP_MCP_DATA_DIR=/home/cppmcp/data
for tag in n3337 n4140 n4659 n4861 n4950 n5046 main; do
    sudo -u cppmcp CPP_MCP_DATA_DIR=$CPP_MCP_DATA_DIR \
        /home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp ingest --tag $tag
done

sudo systemctl restart cpp-mcp
```

For backwards-incompatible schema changes (renamed or removed columns), delete the database first:

```bash
rm /home/cppmcp/data/standard.db
# Then re-ingest all tags as above
```

### Verify after upgrade

```bash
sudo systemctl status cpp-mcp
# Check tool count via MCP or logs
```

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

If you want a specific version to be the default (instead of the highest-numbered tag):

Edit the systemd unit to add `--default-draft`:

```ini
ExecStart=/home/cppmcp/wg21-paperflow/.venv/bin/cpp-mcp serve --host 127.0.0.1 --port 8001 --keys-file /etc/cpp-mcp/keys --default-draft n5046
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart cpp-mcp
```
