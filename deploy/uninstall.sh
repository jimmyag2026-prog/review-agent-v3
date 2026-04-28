#!/usr/bin/env bash
# Default: keep user data (only remove service + code).
# --purge: also tar-backup then remove /var/lib/review-agent + /etc/review-agent + /var/log/review-agent
set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2; exit 2
fi

systemctl disable --now review-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/review-agent.service
rm -f /etc/caddy/Caddyfile.d/review-agent.caddy
systemctl daemon-reload

rm -rf /opt/review-agent

if [ "$PURGE" -eq 1 ]; then
    TS=$(date +%Y%m%d-%H%M%S)
    BACKUP=/var/backups/review-agent
    mkdir -p "$BACKUP"
    if [ -d /var/lib/review-agent ]; then
        tar -czf "$BACKUP/uninstall-$TS.tgz" /var/lib/review-agent /etc/review-agent /var/log/review-agent 2>/dev/null || true
        echo "* user data backed up to $BACKUP/uninstall-$TS.tgz"
    fi
    rm -rf /var/lib/review-agent /etc/review-agent /var/log/review-agent
    userdel review-agent 2>/dev/null || true
    echo "* purged"
else
    echo "* service + code removed; user data preserved at /var/lib/review-agent"
    echo "  re-run with --purge to also remove user data"
fi
