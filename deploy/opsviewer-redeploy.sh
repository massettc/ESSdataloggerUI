#!/bin/bash
# Recreate the opsviewer2-edge container from /opt/opsviewer/opsviewer-env.json
# Run as pi-network-admin (docker group member — no sudo needed for docker commands).
set -e

CONFIG_FILE="/opt/opsviewer/opsviewer-env.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file not found: $CONFIG_FILE" >&2
    exit 1
fi

EDGE_DEVICE_ID=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d['EDGE_DEVICE_ID'])")
EVENTHUB_CONN=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d['EventHub__ConnectionString'])")
IMAGE=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('IMAGE','opsviewer2/edge:r5'))")

if [ -z "$EDGE_DEVICE_ID" ]; then
    echo "ERROR: EDGE_DEVICE_ID is blank in $CONFIG_FILE" >&2
    exit 1
fi

echo "Stopping opsviewer2-edge (if running)..."
docker stop opsviewer2-edge 2>/dev/null || true
docker rm opsviewer2-edge 2>/dev/null || true

echo "Starting opsviewer2-edge with EDGE_DEVICE_ID=$EDGE_DEVICE_ID image=$IMAGE ..."
docker run -d \
    --name opsviewer2-edge \
    --restart unless-stopped \
    -p 8080:8080 \
    -p 1883:1883 \
    -p 9001:9001 \
    -e "EDGE_DEVICE_ID=$EDGE_DEVICE_ID" \
    -e "EventHub__ConnectionString=$EVENTHUB_CONN" \
    "$IMAGE"

echo "Done. Container started."
docker inspect opsviewer2-edge --format 'Status: {{.State.Status}}'
