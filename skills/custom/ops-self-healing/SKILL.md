---
name: ops-self-healing
description: Use this skill when the user reports a broken service, asks to check system health, restart containers, read logs, fix infrastructure issues, or investigate why something is down. Also use proactively when another skill encounters an infrastructure error.
---

# Ops & Self-Healing Skill

## Overview

You have direct access to the evo-server homelab infrastructure via bash. You can diagnose issues, read logs, restart services, edit configs, and fix problems autonomously.

## Server Context

- **Hostname:** evo-server (GMKtec EVO-X2, Ryzen AI Max+ 395, 96GB RAM)
- **OS:** Ubuntu Server 24.04 LTS
- **Docker:** All services run as Docker containers
- **GPU:** Radeon 8060S with ROCm (for Ollama)

## Running Services

### Critical (never stop these)
| Service | Container | Port | Health |
|---------|-----------|------|--------|
| DeerFlow Nginx | deer-flow-nginx | 2026 | curl localhost:2026 |
| DeerFlow Gateway | deer-flow-gateway | 8001 | curl localhost:8001/health |
| DeerFlow LangGraph | deer-flow-langgraph | 2024 | curl localhost:2024/ok |
| PostgreSQL | homelab-postgres | 5432 | docker exec homelab-postgres pg_isready |
| Ollama | (host process) | 11434 | curl localhost:11434/api/tags |

### Important (restart if down)
| Service | Container | Port | Health |
|---------|-----------|------|--------|
| Kalshi Weather | kalshi-weather | 8011 | curl localhost:8011/health |
| Kalshi DB | kalshi-weather-db | 5433 | docker exec kalshi-weather-db pg_isready |
| Home Assistant | homeassistant | 8123 | curl localhost:8123 |
| Zigbee2MQTT | zigbee2mqtt | 8124 | curl localhost:8124 |
| Mosquitto | mosquitto | 1883 | -- |
| Pi-hole | pihole-* | 8081 | curl localhost:8081/admin |
| Outline | outline | 3000 | curl localhost:3000 |

### Infrastructure
| Service | Container | Notes |
|---------|-----------|-------|
| Coolify | coolify | Port 8000, deployment platform |
| Cloudflare Tunnel | cloudflared | systemd service |

## Diagnostic Workflow

1. **Identify the problem**
   ```bash
   docker ps -a --format "table {{.Names}}\t{{.Status}}" | sort
   ```

2. **Check container logs**
   ```bash
   docker logs <container_name> --tail 50
   ```

3. **Check system resources**
   ```bash
   free -h                    # Memory
   df -h /                    # Disk
   rocm-smi                   # GPU utilization
   top -b -n1 | head -20     # CPU/process
   ```

4. **Check networking**
   ```bash
   curl -s http://localhost:<port>/health
   docker network inspect <network_name>
   ss -tlnp | grep <port>
   ```

5. **Fix and verify**
   ```bash
   docker restart <container_name>
   docker logs <container_name> --tail 10
   ```

## Common Fixes

### Container won't start
1. Check logs: `docker logs <name> --tail 50`
2. Check disk space: `df -h /`
3. Check port conflict: `ss -tlnp | grep <port>`
4. Recreate: `cd <service_path> && docker compose up -d`

### Out of VRAM
1. Check: `rocm-smi`
2. Unload unused models: `curl -X DELETE http://localhost:11434/api/generate -d '{"model":"<name>","keep_alive":0}'`
3. Check what's loaded: `curl localhost:11434/api/ps`

### Database issues
1. Check: `docker exec homelab-postgres pg_isready`
2. Logs: `docker logs homelab-postgres --tail 30`
3. Connection test: `docker exec homelab-postgres psql -U homelab -d homelab -c "SELECT 1"`

### Service paths
| Service | Path |
|---------|------|
| DeerFlow | ~/deerflow |
| Kalshi | ~/services/kalshi-trading |
| Home Assistant | ~/services/home-assistant |
| Outline | ~/services/outline |

## Safety Rules

- NEVER delete data without explicit user confirmation
- NEVER stop homelab-postgres, deer-flow-*, or kalshi-* without asking first
- Always check logs BEFORE restarting — understand the root cause
- After any fix, verify the service is healthy
- Report what you found and what you did back to the user
