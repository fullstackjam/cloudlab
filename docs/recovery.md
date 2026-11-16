# Restore CloudLab on another machine

Use this guide when asked to restore this environment on a specified machine. The entry point is [infra/restore.py](../infra/restore.py). It prepares an empty Ubuntu node, restores Git configuration and COS application backups, checks the services, and can take over the existing public endpoints.

A restore reuses the existing repository, age identity, COS bucket, Tunnel, domains, and application credentials. Do not create replacements for those resources. Creating or reinstalling a CVM is a separate operation: verify the target and authorization before changing its system disk. The restore script never formats a disk or removes an existing cluster.

## Gather the inputs

An agent should find these values from the supplied machine, the cloud API, the repository, and the credential vault. Ask for information only when the target or a credential cannot be identified uniquely.

| Input | How to identify it |
| --- | --- |
| Target SSH destination | The machine named in the request; verify its cloud instance, account, region, public address, and SSH identity |
| Data disk UUID | Match the cloud disk ID to the guest disk serial with `lsblk`, then inspect its filesystem and UUID |
| Source SSH destination | The existing CloudLab node, if it is still running; compare its GitRepository URL and machine identity with the target |
| GitHub repository | `git remote get-url origin`; pass `OWNER/REPO` |
| SOPS age private key | The vault entry whose public recipient matches `.sops.yaml` and the committed ciphertext |
| COS backup credentials | The existing object credentials for the restic prefix, plus the separate restic encryption password |
| restic repository URL | The existing bucket, region endpoint, and `restic/cloudlab` prefix; recover it from deployment records or the source node's protected backup configuration |
| Kuma login | The saved login for `status.<domain>`; username and password are both needed |
| ntfy topic URL | The existing notification destination; it must match the intended deployment |

Use host-management credentials for CVM operations and backup object credentials for restic. They can belong to different cloud accounts. Do not assume one key can access both.

The management machine needs Python 3 with PyYAML, Git, SSH, kubectl, Flux, SOPS, and the GitHub CLI. Use the versions in the [bootstrap guide](bootstrap.md). `gh auth token` supplies the GitHub bootstrap token unless `GITHUB_TOKEN` is already set. The token must be able to push to this repository and manage its deploy keys.

For 1Password, list item titles and IDs first and inspect field labels without printing their values. Set credential variables to `op://VAULT/ITEM-ID/FIELD` references. The restore entry reads all referenced items in one `op item get` batch. It does not start concurrent authorization requests or use an outer `op run`. A desktop vault still requires the user's normal authorization. If authentication times out, stop and diagnose it before retrying; do not create new credentials to work around it.

## Prepare an empty target

The target must run Ubuntu 24.04, accept SSH, and allow noninteractive sudo. Verify the system disk and data disk before mounting anything. The script accepts an existing ext4 data disk UUID and checks that it is not the system disk. It mounts `/data`, checks `/etc/fstab`, installs k3s from the repository configuration, and waits for the node and local-path StorageClass.

For an unformatted new data disk, confirm that it is the selected empty disk, format it as ext4, and obtain its UUID before invoking the script. Never select a disk from its device name alone.

After an OS reinstall, the data disk may still contain `/data/k3s`. Decide whether to resume that cluster or perform a clean restore. For a clean restore, stop k3s and its containers, verify that no mounts remain under `/data/k3s`, and move the directory aside before running this workflow. Do not reuse its databases as evidence that a COS restore worked. Keep the old directory until the replacement has passed verification.

## Run the isolated restore

Supply these environment variables through the credential manager or process environment. Do not put plaintext values in shell history, command arguments, the work directory, or Git.

| Variable | Value |
| --- | --- |
| `SOPS_AGE_KEY` | Existing age private key, or its `op://` reference; `SOPS_AGE_KEY_FILE` is also supported |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Existing COS backup object key pair, or their `op://` references |
| `RESTIC_PASSWORD` | Existing restic encryption password, or its `op://` reference |
| `RESTIC_REPOSITORY` | `s3:https://cos.REGION.myqcloud.com/BUCKET-APPID/restic/cloudlab` |
| `KUMA_USERNAME`, `KUMA_PASSWORD` | Saved administrator login, or its `op://` references |
| `NTFY_URL` | `https://ntfy.sh/YOUR-TOPIC` |
| `GITHUB_TOKEN` | Optional override for `gh auth token` |
| `CLOUDLAB_SSH_OPTIONS` | Optional SSH arguments, for example `-i /private/path/key -o IdentitiesOnly=yes` |

Set `CLOUDLAB_TARGET`, `CLOUDLAB_DATA_UUID`, `CLOUDLAB_REPOSITORY`, and `CLOUDLAB_RECOVERY_DIR` to the verified nonsecret inputs. Put the recovery directory outside the repository, with access restricted to the current user. The script stores its checkpoint, isolated checkout, and kubeconfig there; credentials stay in memory. The node's scheduled backup configuration is written to `/etc/cloudlab/backup.env`, owned by root with mode `0600`.

```bash
python3 infra/restore.py \
  --host "$CLOUDLAB_TARGET" \
  --data-uuid "$CLOUDLAB_DATA_UUID" \
  --repository "$CLOUDLAB_REPOSITORY" \
  --work-dir "$CLOUDLAB_RECOVERY_DIR"
```

The default workflow runs four phases:

1. `prepare`: mount the verified disk, install k3s, and check node readiness.
2. `bootstrap`: create and push a temporary recovery branch, set its Flux source before bootstrap, validate every SOPS file, and install Flux. The branch disables cloudflared, the Thanos Compactor, Sidecar uploads, and alert delivery.
3. `data`: select a restic snapshot, restore all three application exports, recreate the Kuma administrator and ntfy notification, reassign monitor ownership, and create a scoped InfluxDB write token. The phone's receiver tokens remain unchanged. Only the new InfluxDB token's ciphertext is committed.
4. `verify`: check Pod readiness, Grafana login and provisioned dashboards, the official Health Auto Export read API, Kuma login and monitor visibility, recent metrics, and a Store Gateway-only query of historical COS metrics.

`--snapshot ID` selects a specific backup. Otherwise the script selects the newest snapshot tagged `cloudlab` for host `cloudlab` and records its full ID. InfluxDB validation compares all timestamps, tags, field types, and values with the export. MongoDB uses authenticated `mongorestore --stopOnError` and reports collection and index counts. Kuma reports monitor and history counts and checks the authenticated monitor list.

The restored node remains isolated. A ready Pod alone is not a successful recovery, and a local service check does not prove that public traffic has moved.

## Activate the restored node

A request to replace the running environment includes cutover after the restored node passes checks. Identify the existing source node before proceeding. If the request is only for an isolated drill, stop after verification.

When the source is reachable, use the same inputs and credentials with this phase:

```bash
python3 infra/restore.py \
  --host "$CLOUDLAB_TARGET" \
  --data-uuid "$CLOUDLAB_DATA_UUID" \
  --repository "$CLOUDLAB_REPOSITORY" \
  --work-dir "$CLOUDLAB_RECOVERY_DIR" \
  --phase activate --source-host "$CLOUDLAB_SOURCE"
```

The script checks that the source is a different machine watching the same repository. It stops the source's reconciliation, public connector, application writers, metrics writers, and notifications, then takes a final backup. It imports that snapshot into the target before switching its Git source to `main`. This causes a service interruption during the final backup, restore, and reconciliation.

If the old node is confirmed stopped or lost, use `--source-unavailable` instead of `--source-host`. This is an assertion about the old node's actual state, not a shortcut around checking it. Never run two Compactors or two Sidecars with the same external labels against the same object prefix.

The activation phase removes the target's isolated Prometheus samples before enabling uploads, so they cannot overlap with production blocks in COS. It does not copy the source's unuploaded Prometheus head or WAL. Metrics collected since the last uploaded block can be missing after cutover; the normal block interval is two hours. If those samples must be preserved, plan a stopped TSDB transfer instead of using this workflow unchanged.

Activation commits the target's scoped InfluxDB token to `main`, waits for Flux to apply that exact revision, checks public endpoints against the target's whoami Pod, and submits an authenticated empty Health Auto Export batch. It also sends a clearly named Alertmanager test alert and checks that it arrives at ntfy. It runs a COS backup on the target and enables the backup timer only after that job succeeds.

For an end-to-end phone check, run the existing iPhone export and compare the receiver's stored record counts and latest timestamps. The empty-batch check verifies authentication and routing; it does not replace a device upload test.

## Resume or recover from a failure

The script prints the failing phase and leaves its checkpoint in the private work directory. Reusing that directory with the default command skips completed phases. `--phase prepare|bootstrap|data|verify|activate` reruns one phase explicitly. Keep using the same target, disk UUID, and repository; the checkpoint rejects a different target.

If `prepare` installed k3s but failed while waiting, inspect the node before resuming at `bootstrap`. Do not rerun host preparation over an existing cluster. During an application import failure, the app reconciliation stays suspended so Flux cannot restart writers over a partial restore. Fix the cause and rerun `data`; the target is still isolated. Do not activate a partial restore.

If activation has already published the target configuration to `main`, rerunning `activate` completes reconciliation and verification without importing again or clearing active metrics. Before that point, the source's data remains on its disk. Its saved workload replica settings are in `/root/cloudlab-before-recovery.json`; use them to restore the source if abandoning the cutover, then resume its Flux controllers and backup timer. Check the active Tunnel connector before enabling either node.

## Finish the handover

Report the restored snapshot, Flux revision, data checks, public whoami identity, notification event, and successful backup job. Wait for the `main` CI run to pass. Explicitly name any check that could not be completed.

The script deletes its temporary branch after successful activation. Keep its active Flux deploy key: the cluster still uses that key when watching `main`. Delete a retired node's deploy key only after confirming it is no longer in use. Remove private recovery files and SSH forwarding processes when they are no longer needed.

Only release a temporary CVM, its disks, and its security group when that cleanup was authorized and the final node has passed the checks above. Verify cloud resource deletion through the cloud API. Do not delete the retained node, COS bucket, Tunnel, production DNS records, or valid backup snapshots as part of temporary resource cleanup.
