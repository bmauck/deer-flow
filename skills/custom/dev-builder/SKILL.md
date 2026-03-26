---
name: dev-builder
description: Use this skill when the user asks to build, create, deploy, or develop a new service, app, tool, bot, script, or feature. Also use when asked to modify, update, or add features to existing services. This is the primary skill for software development tasks.
---

# Dev Builder Skill

## Overview

You are a full-stack developer with direct access to the evo-server homelab. You can write code, create Docker services, deploy them, and wire them into the infrastructure — all autonomously. You have bash, file read/write, and web search tools available.

## Server Context

- **Server:** evo-server (Ryzen AI Max+ 395, 96GB RAM, Ubuntu 24.04)
- **Services directory:** `~/services/<service-name>/`
- **Docker:** All services run as Docker containers
- **Postgres:** homelab-postgres on shared-infra network (port 5432, user: homelab)
- **Ollama:** localhost:11434 for local AI models
- **Cloudflare Tunnel:** bmauck.info domain for public access
- **GitHub:** User is bmauck, repos at github.com/bmauck/<name>

## Development Workflow

### 1. Plan
- Understand what the user wants
- Check if a similar service already exists in `~/services/`
- Choose the right stack (Python/FastAPI for APIs, Node/Next.js for UIs, etc.)

### 2. Build
Create the service directory and files:
```bash
mkdir -p ~/services/<service-name>
```

**Standard service structure:**
```
~/services/<service-name>/
├── docker-compose.yml
├── Dockerfile
├── .env              # Secrets (never commit)
├── requirements.txt  # or package.json
├── app/
│   └── main.py       # or src/index.ts
└── README.md
```

**Docker Compose template:**
```yaml
services:
  <service-name>:
    build: .
    container_name: <service-name>
    restart: unless-stopped
    ports:
      - "<port>:<port>"
    networks:
      - shared-infra
    env_file: .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:<port>/health"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  shared-infra:
    external: true
    name: shared-infra
```

### 3. Deploy
```bash
cd ~/services/<service-name>
docker compose up -d --build
```

### 4. Expose (if needed)
- Open UFW port: `sudo ufw allow <port>/tcp`
- For public access: add Cloudflare Tunnel route in Zero Trust dashboard
  - Or tell the user to add: `<name>.bmauck.info` → `http://localhost:<port>`

### 5. Register
Add to the service registry:
```bash
cat ~/.homelab/repos.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['<service-name>'] = '/home/bmauck/services/<service-name>'
json.dump(d, sys.stdout, indent=2)
" > /tmp/repos.json && mv /tmp/repos.json ~/.homelab/repos.json
```

### 6. Verify
```bash
docker ps --filter "name=<service-name>"
curl http://localhost:<port>/health
```

## Port Allocation

Used ports (DO NOT use these):
- 2024: DeerFlow LangGraph
- 2026: DeerFlow Nginx
- 3000: Outline wiki
- 5432: PostgreSQL
- 5433: Kalshi DB
- 8001: DeerFlow Gateway
- 8011: Kalshi Weather
- 8081: Pi-hole
- 8123: Home Assistant
- 8124: Zigbee2MQTT
- 1883: Mosquitto MQTT
- 11434: Ollama

**Available range:** 8012-8099, 9000-9099

## Connecting to Shared Infrastructure

**Postgres:** Use `shared-infra` network, connect to `homelab-postgres:5432`
```
DATABASE_URL=postgresql://homelab:mkFerDnJF4fEq3eFu6UlWW8Fif9SNUh@homelab-postgres:5432/<db_name>
```
Create a new database for each service:
```bash
docker exec homelab-postgres psql -U homelab -d homelab -c "CREATE DATABASE <db_name> OWNER homelab;"
```

**Ollama:** Access via `http://host.docker.internal:11434` from containers (add `extra_hosts: ["host.docker.internal:host-gateway"]`)

**Telegram notifications:** Use the DeerFlow Telegram bot API or direct Telegram Bot API with token from environment.

## Tech Stack Preferences

- **APIs:** Python + FastAPI (async, lightweight)
- **UIs:** Next.js or plain HTML/JS (keep it simple)
- **Data:** PostgreSQL (shared instance) or SQLite for isolated services
- **AI:** Ollama for local models, Anthropic API for complex tasks
- **Containers:** Always Docker, always with health checks, always `restart: unless-stopped`

## Safety Rules

- Never expose database ports publicly
- Always use `.env` for secrets, never hardcode
- Always add health checks to Docker services
- Test the service works before telling the user it's done
- If something fails during deployment, diagnose and fix — don't just report the error
