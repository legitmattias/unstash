#!/usr/bin/env bash
#
# Prepare a fresh Ubuntu host to run the Unstash compose stack.
#
# Idempotent: every step checks the current state first, so a second run
# changes nothing and a failed run can be repeated.
#
# Installs Docker and Caddy, creates the service and deploy identities and
# the per-environment directories, hardens SSH, enables the host firewall,
# and installs the nightly database backup timer.
#
# It does not create secrets, proxy site files or DNS records — see
# docs/runbooks/host-setup.md for those steps.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: DEPLOY_PUBKEY="ssh-ed25519 AAAA..." host-bootstrap.sh [--help]

Run as root on the target host.

Environment:
  DEPLOY_PUBKEY   Required. Public key the deploy workflow authenticates with.
  DEPLOY_USER     Login the deploy workflow uses.          Default: deploy
  ENVIRONMENTS    Space-separated environment names.       Default: "staging production"
  DATA_ROOT       Parent of <env>/documents.               Default: /var/lib/unstash
  SECRETS_ROOT    Parent of <env>/ secret directories.     Default: /opt/unstash/secrets
  BACKUP_ROOT     Parent of <env>/ database dumps.         Default: /var/backups/unstash
EOF
}

if [[ "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "error: must run as root" >&2
    exit 1
fi

: "${DEPLOY_PUBKEY:?DEPLOY_PUBKEY must be set to the public key of the deploy workflow}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
ENVIRONMENTS="${ENVIRONMENTS:-staging production}"
DATA_ROOT="${DATA_ROOT:-/var/lib/unstash}"
SECRETS_ROOT="${SECRETS_ROOT:-/opt/unstash/secrets}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/unstash}"

# The application containers run as uid 1000. Files they read through bind
# mounts (secrets, documents) must be owned by that uid on the host.
SERVICE_USER="unstash"
SERVICE_UID=1000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { printf '\n==> %s\n' "$1"; }

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
step "Base packages"
# ---------------------------------------------------------------------------
apt-get update -q
apt-get install -y -q --no-install-recommends \
    ca-certificates curl gnupg ufw unattended-upgrades

# ---------------------------------------------------------------------------
step "Docker Engine and Compose plugin"
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
        >/etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# Container logs are unbounded by default and fill the disk.
daemon_json='{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" }
}'
if [[ "$(cat /etc/docker/daemon.json 2>/dev/null || true)" != "$daemon_json" ]]; then
    printf '%s\n' "$daemon_json" >/etc/docker/daemon.json
    systemctl restart docker
fi
systemctl enable --now docker

# ---------------------------------------------------------------------------
step "Caddy reverse proxy"
# ---------------------------------------------------------------------------
if ! command -v caddy >/dev/null; then
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        >/etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q
    apt-get install -y -q caddy
fi

# One site file per environment, added by the operator; the main file only
# imports them. An empty directory is a valid configuration.
install -d -m 0755 /etc/caddy/sites
# Site files write their access logs here; the service runs as `caddy`.
install -d -m 0750 -o caddy -g caddy /var/log/caddy
caddyfile='import /etc/caddy/sites/*.caddy'
if [[ "$(cat /etc/caddy/Caddyfile 2>/dev/null || true)" != "$caddyfile" ]]; then
    printf '%s\n' "$caddyfile" >/etc/caddy/Caddyfile
fi
systemctl enable --now caddy
systemctl reload caddy

# ---------------------------------------------------------------------------
step "Service identity (${SERVICE_USER}, uid ${SERVICE_UID})"
# ---------------------------------------------------------------------------
existing="$(getent passwd "$SERVICE_UID" | cut -d: -f1 || true)"
if [[ -z "$existing" ]]; then
    # The group id is set explicitly: a system account otherwise gets a gid
    # from the system range, which would not match the container's.
    groupadd --gid "$SERVICE_UID" "$SERVICE_USER"
    useradd --system --uid "$SERVICE_UID" --gid "$SERVICE_UID" \
        --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
elif [[ "$existing" != "$SERVICE_USER" ]]; then
    echo "error: uid ${SERVICE_UID} belongs to '${existing}', expected '${SERVICE_USER}'" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
step "Deploy identity (${DEPLOY_USER})"
# ---------------------------------------------------------------------------
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
deploy_home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "${deploy_home}/.ssh"
auth_keys="${deploy_home}/.ssh/authorized_keys"
touch "$auth_keys"
if ! grep -qxF "$DEPLOY_PUBKEY" "$auth_keys"; then
    printf '%s\n' "$DEPLOY_PUBKEY" >>"$auth_keys"
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$auth_keys"
chmod 0600 "$auth_keys"

# ---------------------------------------------------------------------------
step "Directories"
# ---------------------------------------------------------------------------
install -d -m 0755 "$DATA_ROOT" "$SECRETS_ROOT"
install -d -m 0700 "$BACKUP_ROOT"
for env in $ENVIRONMENTS; do
    install -d -m 0755 "${DATA_ROOT}/${env}"
    install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "${DATA_ROOT}/${env}/documents"
    install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "${SECRETS_ROOT}/${env}"
    install -d -m 0700 "${BACKUP_ROOT}/${env}"
done

# ---------------------------------------------------------------------------
step "SSH hardening"
# ---------------------------------------------------------------------------
# sshd takes the first value it reads for each keyword, and drop-ins are read
# in name order, so this file must sort before any distribution drop-in.
sshd_dropin='PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password'
if [[ "$(cat /etc/ssh/sshd_config.d/00-hardening.conf 2>/dev/null || true)" != "$sshd_dropin" ]]; then
    printf '%s\n' "$sshd_dropin" >/etc/ssh/sshd_config.d/00-hardening.conf
    sshd -t
    systemctl try-reload-or-restart ssh
fi

# ---------------------------------------------------------------------------
step "Host firewall"
# ---------------------------------------------------------------------------
# Docker publishes ports through its own iptables chains and bypasses ufw, so
# this protects host services only. Container ports stay private because the
# compose files bind them to the loopback interface.
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed -n '1,12p'

# ---------------------------------------------------------------------------
step "Unattended security upgrades"
# ---------------------------------------------------------------------------
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

# ---------------------------------------------------------------------------
step "Nightly database backup"
# ---------------------------------------------------------------------------
install -m 0755 "${SCRIPT_DIR}/backup-database.sh" /usr/local/sbin/unstash-backup-database

cat >/etc/systemd/system/unstash-backup@.service <<EOF
[Unit]
Description=Unstash database dump (%i)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
Environment=BACKUP_ROOT=${BACKUP_ROOT}
ExecStart=/usr/local/sbin/unstash-backup-database %i
EOF

cat >/etc/systemd/system/unstash-backup@.timer <<'EOF'
[Unit]
Description=Nightly Unstash database dump (%i)

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
for env in $ENVIRONMENTS; do
    systemctl enable --now "unstash-backup@${env}.timer"
done

step "Done"
echo "Next: secrets, proxy site files and DNS — docs/runbooks/host-setup.md"
