# Runbook: VPS Maintenance

## Purpose

Operational procedures for the Hetzner VPS hosting Unstash: tier upgrades, volume management, OS patching, reboots, and recovery from common stuck-state scenarios.

## When to use it

- Scheduled monthly patch + reboot cycle.
- Resource pressure (memory or CPU) requiring a tier upgrade.
- Storage growth requiring an additional or expanded volume.
- Post-host-reboot recovery of the stack.
- Disk-full, OOM, or container-stuck troubleshooting.

## Prerequisites

- SSH access to the production VPS as a user in the `docker` group.
- Hetzner Cloud Console access with permissions to rescale the server and manage volumes.
- `hcloud` CLI installed with a context pointing at the relevant Hetzner project.
- Working local Docker context configured to reach the remote Docker daemon over SSH.
- Awareness that this VPS may host services beyond Unstash. Actions that reboot the host affect every service on the box.

## Configuration placeholders

Procedures below refer to these placeholders. Substitute your actual values from your operator configuration when running commands.

| Placeholder | Meaning |
|---|---|
| `$HOST` | SSH alias or `hcloud` server identifier for the production VPS |
| `$DOCKER_CONTEXT` | Docker context name pointing at the remote Docker daemon |
| `$DATA_MOUNT` | Mount path of the attached data volume (used for Postgres data and backup repository) |
| `$PROD_COMPOSE` | Path to the production Compose file (typically `compose.yaml` + `compose.production.yaml`) |
| `$STAGING_COMPOSE` | Path to the staging Compose file (typically `compose.yaml` + `compose.staging.yaml`) |

---

## 1. Tier upgrade (rescale within the CX line)

The Hetzner CX line has a fixed primary disk size on all tiers — rescaling changes vCPU and RAM only. Storage growth happens via Volumes (§2).

### When

- Memory pressure shows in `free -h` (swap growing, cache shrinking), or logs show OOM-killed containers.
- Planned ahead of a milestone that adds memory-heavy ML components (reranker, NER, OCR).

### Procedure

1. Announce a short maintenance window. Rescale takes ~5 minutes during which the instance is powered off.
2. Ensure no long-running jobs are mid-transaction. Drain Taskiq queues if needed.
3. Gracefully stop the Docker Compose stack:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE down
   ```
4. In Hetzner Cloud Console → Servers → select the production VPS → **Rescale**, select the target tier, confirm.
5. Hetzner powers the server down, rescales, powers it back on. Wait for the server to return to "running" state and become pingable.
6. SSH in and verify resources: `free -h`, `nproc`, `df -h`.
7. Start the Compose stack again:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE up -d
   ```
8. Verify services are healthy: `/api/health` returns 200, frontend loads.

### Verification

- `free -h` shows the expected new RAM total.
- `nproc` shows the expected new vCPU count.
- All containers show `Up` in `docker ps`.
- No error logs in the 5 minutes after startup.

### Rollback

In-place rescale is reversible via another rescale down in the Cloud Console, but cost savings from downscaling rarely justify the downtime. If the new tier is unstable for any reason, the path is typically forward (up), not back.

---

## 2. Add a new volume

### When

- Adding storage for Postgres data, pgBackRest repository, or other data that must survive instance destruction.
- Provisioning ahead of growth before disk pressure is a concern.

### Procedure

**If the volume is created with the "Automatic" mount option** (Hetzner handles everything):

1. In Hetzner Cloud Console → Volumes → **Create Volume**.
2. Set:
   - Size: at least the anticipated 12-month working-set size.
   - Location: must match the production VPS's location (volumes are location-bound).
   - Server: attach to the production VPS.
   - **Mount options: Automatic**
   - Filesystem: EXT4.
3. Hetzner formats, attaches, and auto-mounts the volume at `/mnt/HC_Volume_<id>`. An `/etc/fstab` entry using `/dev/disk/by-id/scsi-0HC_Volume_<id>` is created so the mount survives reboots.
4. SSH to the production VPS to verify:
   ```
   df -h | grep HC_Volume
   lsblk
   ```
5. The volume is ready to use at its `/mnt/HC_Volume_<id>` path. Subdirectories can be created as needed.

**If the volume is created with "Manually"** (manual formatting and mount):

1. Create the volume in the Console, attach to the server, skip the Automatic option.
2. SSH to the production VPS and check `lsblk` for the new block device (e.g., `/dev/sdc`).
3. Format: `sudo mkfs.ext4 /dev/sdX`.
4. Create a mount point: `sudo mkdir -p /mnt/<name>`.
5. Add an `/etc/fstab` entry using `/dev/disk/by-id/scsi-0HC_Volume_<id>` as the source — never raw `/dev/sdX`, which can reorder across reboots.
6. `sudo mount -a` and verify with `df -h`.

### Verification

- `df -h` shows the volume mounted at the expected path with the expected size.
- Reboot the instance (planned, during a maintenance window) to confirm it auto-mounts.

---

## 3. Expand an existing volume

### When

- Disk usage on a volume is approaching 80% and forecasted to continue.
- Migrating a larger corpus onto the same volume.

### Procedure

1. In Hetzner Cloud Console → Volumes → select the volume → **Resize**.
2. Set the new size. Cost increases at the next billing cycle.
3. Hetzner resizes the underlying block device immediately; the server sees the larger block device without reboot.
4. Extend the filesystem on the server:
   ```
   sudo resize2fs /dev/sdX
   ```
   (using the actual device path).
5. Verify with `df -h` that the new size is reflected.

### Verification

- `df -h` shows the increased size.
- Data on the volume remains intact — `resize2fs` only extends the filesystem into the new space.

---

## 4. Migrate Postgres data from instance disk to a volume

A one-time operation, planned and scripted. Do this while the database is small rather than after gigabytes of embeddings have accumulated.

### Procedure

1. Provision the target volume following §2. Below the target path is referred to as `$DATA_MOUNT`.
2. Announce a maintenance window. Data migration is not zero-downtime with this procedure.
3. SSH to the production VPS.
4. Stop the Postgres container only:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE stop postgres
   ```
5. Identify the current Postgres data path. If using a Docker-managed volume:
   ```
   docker volume inspect <project-pgdata-volume>
   ```
   Note the `Mountpoint` field (e.g., `/var/lib/docker/volumes/<name>/_data`).
6. Copy the data to the mounted volume:
   ```
   sudo mkdir -p $DATA_MOUNT/postgres/production
   sudo rsync -av --progress <source-mountpoint>/ $DATA_MOUNT/postgres/production/
   ```
   Use a parallel subdirectory (`$DATA_MOUNT/postgres/staging/`) for the staging Postgres when migrating that environment.
7. Update the relevant Compose file to bind-mount the new path instead of the Docker-managed volume:
   ```yaml
   services:
     postgres:
       volumes:
         - $DATA_MOUNT/postgres/production:/var/lib/postgresql/data
   ```
8. Re-apply via the deploy workflow (commit the compose change, push, CI deploys).
9. Start the stack; verify Postgres is serving data correctly.

### Verification

- Postgres container is up.
- `psql` queries return expected data (row counts match pre-migration).
- Disk usage on `/var/lib/docker/volumes/` has dropped; disk usage on `$DATA_MOUNT/postgres/...` matches the expected data size.

### Rollback

If Postgres fails to start from the new path, revert the Compose change and restart. The original Docker volume is untouched by the rsync (it's a copy, not a move). Only after successful verification should the old volume be removed.

---

## 5. Monthly OS patching and reboot

### When

- Monthly, at a cadence configured in the operator's calendar.
- Immediately after the cloud provider announces emergency maintenance.
- When `unattended-upgrades` reports pending kernel patches.

### Procedure

1. Announce a short maintenance window (typically 10-15 minutes).
2. SSH to the production VPS.
3. Pull the latest package lists:
   ```
   sudo apt update
   ```
4. Apply all security and regular updates:
   ```
   sudo apt upgrade -y
   ```
5. Check if a reboot is required:
   ```
   cat /var/run/reboot-required 2>/dev/null
   ```
   If the file exists, a reboot is needed (typically after kernel updates).
6. If reboot required, stop the Compose stack cleanly first:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE down
   ```
7. Reboot:
   ```
   sudo reboot
   ```
8. Wait for the server to come back online (SSH reconnects).
9. Docker containers with `restart: unless-stopped` or `restart: always` return automatically. Verify:
   ```
   docker ps
   ```
10. Check health endpoints: `/api/health` for Unstash, plus equivalent endpoints for any other services running on this host.

### Verification

- All expected containers are running.
- `/api/health` returns 200.
- No errors in `journalctl -u docker -n 100` since reboot.
- `uptime` shows a fresh boot time.

### Rollback

Kernel rollback to the previous version is via GRUB at next boot. On Hetzner this requires console access (Cloud Console → Servers → Recovery). In practice, kernel rollback is rarely needed — if a kernel update breaks something, restoring from a cloud-provider snapshot is usually simpler.

---

## 6. Troubleshooting: disk full

### Symptoms

- Container writes fail.
- Postgres refuses new connections or errors on commit.
- `df -h` shows 95%+ usage on some filesystem.

### Procedure

1. Identify which filesystem is full:
   ```
   df -h
   ```
2. Find the largest consumers:
   ```
   sudo du -h --max-depth=1 / 2>/dev/null | sort -rh | head -20
   sudo du -h --max-depth=2 /var/lib/docker 2>/dev/null | sort -rh | head -20
   ```
3. Common fixes:
   - Docker overlay bloat: `docker system prune -af --volumes` (destroys unused images and containers — be careful about "unused"; stopped containers with valuable state count as unused).
   - Log bloat: truncate or rotate large log files under `/var/log` and `/var/lib/docker/containers/*/`.
   - Postgres growth on the instance disk: migrate to a volume (§4).
4. Re-run `df -h` to confirm space recovered.

### Verification

- Filesystem usage below 80%.
- Writes succeed (touch a test file on the affected volume).

---

## 7. Troubleshooting: host OOM or memory pressure

### Symptoms

- Containers restarting unexpectedly.
- `dmesg` shows `Out of memory: Killed process <PID>`.
- `free -h` shows swap growing, cache shrinking.

### Procedure

1. Confirm the pattern:
   ```
   sudo dmesg | grep -i "killed process" | tail -20
   ```
2. Identify memory-heavy containers:
   ```
   docker stats --no-stream
   ```
3. Short-term mitigation: restart the offending container to reclaim memory. This is a band-aid, not a fix.
4. Structural fixes (in order of preference):
   - Rescale the VPS to a larger tier (§1).
   - Offload heavy ML inference to external APIs instead of running models locally.
   - Reduce container memory limits where possible.
   - Investigate memory leaks in application code if one container's memory grows unboundedly.

### Verification

- `docker stats` shows stable memory use.
- No new OOM kills in `dmesg`.

---

## 8. Troubleshooting: stuck stack after host reboot

### Symptoms

- Containers show `Restarting` in `docker ps`.
- Health endpoints return connection refused.
- Logs show a service starting before its dependency is ready.

### Procedure

1. Check which services are stuck:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE ps
   ```
2. Check why one is stuck — typical causes:
   - Postgres not ready before the app tries to connect. Compose `depends_on: { condition: service_healthy }` should prevent this; verify it is configured.
   - Volume not mounted (e.g., `/etc/fstab` entry broken after a kernel update).
   - Network out of sync between containers.
3. If dependency ordering is the problem:
   ```
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE down
   docker --context $DOCKER_CONTEXT compose -f $PROD_COMPOSE up -d
   ```
4. If volumes are the problem, verify mounts and re-run `sudo mount -a`.

### Verification

- All services `Up` and health-checked.
- No restart loops visible in `docker ps` over a 5-minute window.

---

## Cadenced operations

| Cadence | Task | Procedure |
|---|---|---|
| Monthly | OS patch + reboot | §5 |
| Monthly | Check disk growth trends (`df -h`, note deltas) | Manual, via SSH |
| Quarterly | End-to-end backup-restore drill | `restore-from-backup.md` (to be written) |
| Annually | Review VPS tier vs. actual load, consider upgrade or downgrade | §1 |

Calendar entries for these are maintained by the operator outside this repo.

## Escalation

If a procedure here does not resolve the issue and the system is in a degraded state:

- Restore from a cloud-provider snapshot (if one exists and is recent enough).
- Provision a fresh VPS and redeploy from the deploy workflow's most recent successful run; restore Postgres data from the most recent pgBackRest backup.
- Preserve logs and `dmesg` output from the affected instance for post-mortem analysis before destroying.

## References

- `docs/adr/0003-file-based-secrets-on-vps.md` — secrets lifecycle on the VPS.
- Hetzner Cloud documentation — volumes, server lifecycle, rescaling, backups.
