#!/usr/bin/env bash
# Deploy compress.applesauce.chat to the mat host.
#
#   ./deploy.sh            # frontend (rsync static files -> mat:/opt/compress)
#   ./deploy.sh backend    # backend  (dl-service.py -> mat:/opt/compress-dl + restart)
#   ./deploy.sh all        # both
#
# Reminder: bump CACHE_NAME in sw.js in the same commit as any frontend change,
# or clients keep the old app shell until the SW happens to update.
set -euo pipefail
cd "$(dirname "$0")"

HOST=mat
FRONT_DEST=/opt/compress
BACK_DEST=/opt/compress-dl
UNIT=compress-dl.service

what="${1:-frontend}"

deploy_frontend() {
    echo "==> rsync static files -> $HOST:$FRONT_DEST"
    # ssh lands as wil; /opt/compress is crawler-owned -> escalate the remote
    # rsync via passwordless sudo and keep the crawler ownership convention.
    rsync -av --delete \
        --rsync-path="sudo rsync" \
        --chown=crawler:crawler \
        --exclude .git \
        --exclude tests \
        --exclude __pycache__ \
        --exclude .pytest_cache \
        --exclude dl-service.py \
        --exclude deploy.sh \
        --exclude '.claude*' \
        --exclude CLAUDE.md \
        --exclude '*.bak.*' \
        --exclude '*.bak2.*' \
        ./ "$HOST:$FRONT_DEST/"
}

deploy_backend() {
    echo "==> scp dl-service.py -> $HOST:$BACK_DEST + restart $UNIT"
    scp dl-service.py "$HOST:/tmp/dl-service.py.new"
    ssh "$HOST" "sudo mv /tmp/dl-service.py.new $BACK_DEST/dl-service.py && sudo systemctl restart $UNIT"
    sleep 2
    ssh "$HOST" "systemctl is-active $UNIT"
}

case "$what" in
    frontend) deploy_frontend ;;
    backend)  deploy_backend ;;
    all)      deploy_frontend; deploy_backend ;;
    *) echo "usage: $0 [frontend|backend|all]" >&2; exit 1 ;;
esac

echo "==> health check"
curl -fsS https://compress.applesauce.chat/api/health && echo
echo "==> done"
