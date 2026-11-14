# Host setup

## Purpose

Prepare a fresh host to run one or more Unstash environments, from a bare operating system to the point where the deploy workflow can deploy to it.

## When to use it

- Bringing a new application host into service.
- Rebuilding a host after loss, before restoring data onto it.
- Checking an existing host against the expected baseline: the bootstrap script is idempotent and reports what it changes.

## Prerequisites

- A host running Ubuntu 24.04 with root SSH access by key.
- A provider-level firewall in front of the host allowing inbound 22, 80 and 443 only.
- A key pair for the deploy workflow. Generate one per host; do not reuse an operator key:

  ```bash
  ssh-keygen -t ed25519 -N "" -C "unstash-deploy" -f ./deploy_key
  ```

- Control of the DNS records for the environment hostnames.
- The secret values for each environment (see `secrets/README.md`).

## What the layout is

Everything lives on the host's local disk, so a disk-level backup of the host covers the database, the uploaded documents and the nightly database dumps together.

| Path | Holds | Owner |
|---|---|---|
| `$DATA_ROOT/<environment>/documents` | Uploaded documents (bind-mounted into api and worker) | service identity |
| `$SECRETS_ROOT/<environment>/` | One file per secret | service identity |
| `$BACKUP_ROOT/<environment>/` | Nightly database dumps | root |
| Docker named volumes | Postgres data, Redis data, model cache | Docker |

Two identities are created, deliberately separate:

- **Service identity** (`unstash`, uid 1000, no login). The application containers run as uid 1000, so files they read through bind mounts must be owned by that uid on the host.
- **Deploy identity** (`deploy` by default). The login the deploy workflow uses. It is a member of the `docker` group and needs no access to the secret or document directories: the Docker daemon performs the mounts.

Membership of the `docker` group is equivalent to root on the host. Treat the deploy key accordingly.

## Procedure

### 1. Run the bootstrap script

Copy `scripts/` to the host and run the script as root:

```bash
scp -r scripts root@$HOST:/root/unstash-scripts
ssh root@$HOST "DEPLOY_PUBKEY='$(cat deploy_key.pub)' /root/unstash-scripts/host-bootstrap.sh"
```

`host-bootstrap.sh --help` lists the variables that change the defaults (environment names, directory roots, deploy user name).

The script installs Docker and Caddy, creates both identities and the per-environment directories, disables SSH password authentication, enables the host firewall and unattended security upgrades, caps container log size, and installs a nightly database dump timer per environment.

Verify:

```bash
ssh root@$HOST 'docker --version && docker compose version && caddy version && ufw status'
ssh -i deploy_key deploy@$HOST 'docker ps'
```

The second command proves the deploy identity can reach Docker.

### 2. Place the secrets

For each environment, create one file per secret in `$SECRETS_ROOT/<environment>/`, owned by the service identity and readable by it alone:

```bash
install -m 0600 -o unstash -g unstash /dev/stdin $SECRETS_ROOT/<environment>/<secret_name>
```

(paste the value, then Ctrl-D). File names carry no extension. The database role passwords are set when the database is first initialised, so on a new host they can be freshly generated; they do not need to match another host's.

Verify that nothing in the directory is readable by other users:

```bash
find $SECRETS_ROOT -type f ! -perm 0600
```

Expect no output.

### 3. Add the proxy site

For each environment, copy `docker/Caddyfile.snippet` to `/etc/caddy/sites/<environment>.caddy`, fill in the hostname and the two loopback ports from that environment's compose override, then:

```bash
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```

Validate as the `caddy` user, not as root. Validation opens the access log file, and a file created by root is unwritable for the service: the reload is then rejected and the previous configuration stays in effect.

Caddy obtains a certificate the first time the hostname resolves to this host, so until DNS points here the site serves no valid certificate. That is expected.

### 4. Point the deploy workflow at the host

In the repository's deployment environment settings, set:

| Kind | Name | Value |
|---|---|---|
| Secret | `DEPLOY_HOST` | the host's address |
| Secret | `DEPLOY_USER` | the deploy identity |
| Secret | `DEPLOY_SSH_KEY` | the deploy private key |
| Variable | `SECRETS_DIR` | `$SECRETS_ROOT/<environment>` |
| Variable | `DOCUMENTS_DIR` | `$DATA_ROOT/<environment>/documents` |

Then delete the local copy of the private key.

### 5. Deploy, then move DNS

Run the deploy workflow for the environment. It pulls the images, applies migrations to the empty database and starts the stack. Check it on the host before any traffic arrives:

```bash
ssh deploy@$HOST 'curl -sf http://127.0.0.1:<api-port>/api/ready'
```

If data is being carried over from another host, restore it now, before moving DNS — see `restore-from-backup.md`.

Point the environment's DNS record at the host. Caddy obtains the certificate on the first request.

## Verification

- `https://<hostname>/api/ready` returns 200 with a valid certificate.
- `systemctl list-timers 'unstash-backup@*'` shows a timer per environment.
- After the first night, `ls $BACKUP_ROOT/<environment>/` shows a dump. To check without waiting: `systemctl start unstash-backup@<environment>.service`.
- `docker inspect --format '{{.HostConfig.LogConfig}}' <any container>` shows the size cap.

## Rollback

Until DNS is moved, the host receives no traffic and can simply be abandoned. After DNS is moved, point the record and the deployment environment settings back at the previous host; nothing on the previous host is changed by this procedure.

## Escalation

If the bootstrap script fails, it stops at the failing step; fix the cause and run it again — completed steps are skipped. If the deploy identity cannot reach Docker, confirm group membership with `id deploy` and start a new SSH session, since group changes do not apply to an existing one.

## Known gaps

- The nightly dump and the host's disk-level backup both live with the same provider. There is no off-provider copy.
- Recovery is to the last nightly dump or disk image; there is no point-in-time recovery.
