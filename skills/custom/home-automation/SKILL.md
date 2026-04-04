---
name: home-automation
description: Use this skill when the user asks about smart home devices, sensors, automations, Zigbee devices, MQTT messages, Home Assistant entities or dashboards, or home automation tasks. Do NOT use for general infrastructure health checks or service restarts — use ops-self-healing instead.
---

# Home Automation Skill

## Overview

You manage the home automation stack on evo-server: Home Assistant, Zigbee2MQTT, and Mosquitto MQTT broker. All three run as Docker containers with host networking.

## Architecture

```
Zigbee devices (Aqara sensors, etc.)
    ↓ Zigbee 3.0
Sonoff USB Dongle Plus V2 (/dev/ttyUSB0)
    ↓
Zigbee2MQTT (port 8124)
    ↓ MQTT
Mosquitto (port 1883)
    ↓ MQTT auto-discovery
Home Assistant (port 8123)
```

**Paths:**
- Docker Compose: `~/services/home-assistant/docker-compose.yml`
- HA config: `~/services/home-assistant/homeassistant/`
- Zigbee2MQTT config: `~/services/home-assistant/zigbee2mqtt/`
- Mosquitto config: `~/services/home-assistant/mosquitto/`

**URLs:**
- HA UI: http://localhost:8123 or https://sensors.bmauck.info
- Zigbee2MQTT UI: http://localhost:8124
- HA API: http://localhost:8123/api/

## Home Assistant API

All API calls require a long-lived access token. Check for it in the environment or HA config.

```bash
# Get HA token (check .env or config)
HA_TOKEN="<long-lived-access-token>"

# List all entities
curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states | python3 -m json.tool | head -50

# Get a specific entity state
curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states/sensor.front_door_contact

# Call a service (e.g., toggle a switch)
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.living_room"}' \
  http://localhost:8123/api/services/switch/toggle

# Fire an event
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event_type": "custom_event", "event_data": {"key": "value"}}' \
  http://localhost:8123/api/events/custom_event
```

## Known Devices

### Zigbee (paired via Zigbee2MQTT)
- **Aqara Door Sensors** (3x MCCGQ11LM) — contact sensors, auto-discovered in HA via MQTT
  - Entities: `binary_sensor.<friendly_name>_contact`
  - MQTT topic: `zigbee2mqtt/<friendly_name>`

### Entity Naming Conventions
- Zigbee devices: auto-named by Zigbee2MQTT friendly_name → HA entity
- Format: `<domain>.<friendly_name_snake_case>` (e.g., `binary_sensor.front_door_contact`)

## Zigbee2MQTT

```bash
# Check Zigbee2MQTT logs
docker logs zigbee2mqtt --tail 30

# View paired devices
cat ~/services/home-assistant/zigbee2mqtt/database.db | python3 -m json.tool 2>/dev/null || cat ~/services/home-assistant/zigbee2mqtt/database.db

# Enable pairing mode (120 seconds) via MQTT
mosquitto_pub -h localhost -t 'zigbee2mqtt/bridge/request/permit_join' -m '{"value": true, "time": 120}'

# Rename a device
mosquitto_pub -h localhost -t 'zigbee2mqtt/bridge/request/device/rename' -m '{"from": "old_name", "to": "new_name"}'

# Get bridge state
mosquitto_sub -h localhost -t 'zigbee2mqtt/bridge/state' -C 1
```

## MQTT Debugging

```bash
# Subscribe to all Zigbee2MQTT topics (watch live messages)
mosquitto_sub -h localhost -t 'zigbee2mqtt/#' -v

# Subscribe to a specific device
mosquitto_sub -h localhost -t 'zigbee2mqtt/Front Door' -v

# Publish a test message
mosquitto_pub -h localhost -t 'test/topic' -m 'hello'

# Check Mosquitto logs
docker logs mosquitto --tail 30
```

## Creating Automations

HA automations live in `~/services/home-assistant/homeassistant/automations.yaml`. You can also create them via the API.

```bash
# Example: Create automation via editing the YAML file
# After editing, reload automations:
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" \
  http://localhost:8123/api/services/automation/reload
```

**Automation YAML structure:**
```yaml
- id: 'unique_id'
  alias: 'Door opened notification'
  trigger:
    - platform: state
      entity_id: binary_sensor.front_door_contact
      to: 'on'
  action:
    - service: notify.notify
      data:
        message: 'Front door was opened!'
```

## Common Tasks

1. **Check sensor state:** Query HA API for entity state
2. **Pair new Zigbee device:** Enable pairing via MQTT, put device in pairing mode, check Zigbee2MQTT logs
3. **Rename device:** Use Zigbee2MQTT MQTT rename command, then verify in HA
4. **Create automation:** Edit automations.yaml or use HA API, then reload
5. **Debug connectivity:** Check Mosquitto → Zigbee2MQTT → HA chain via logs and MQTT subscribe
6. **Restart stack:** `cd ~/services/home-assistant && docker compose restart`

## Safety Rules

- Never delete the Zigbee2MQTT database — it contains all device pairings
- Don't modify `configuration.yaml` without reading it first — syntax errors break HA
- After editing YAML configs, always check HA logs: `docker logs homeassistant --tail 20`
- The USB dongle at `/dev/ttyUSB0` must not be used by multiple processes
