# Harbor on mk4

A private OCI registry for the personal projects, so their images stop living in (and being pulled from) ECR.
Official chart [`harbor/harbor` 1.19.4](https://github.com/goharbor/harbor-helm/tree/v1.19.4) (Harbor v2.15.4), one
Argo CD Application, opt-in: `deploy.py all` never selects it.

| | |
|---|---|
| Portal | https://registry.jithendar.com/harbor/ (https://jithendar.com/harbor redirects there) |
| Registry | `registry.jithendar.com/<project>/<image>:<tag>` |
| Argo CD | Application `harbor`, AppProject `mk-registry`, namespace `harbor` ([`../applications/optional/`](../applications/optional/)) |
| Files | [`values.yaml`](values.yaml) (chart), [`manifests/`](manifests/) (database, Redis, TLS, routes, backup), [`credentials.py`](credentials.py), [`deploy-harbor.sh`](deploy-harbor.sh) (build, push, deploy), [`examples/`](examples/) |

Status on 2026-11-06: nothing is deployed. DNS, images and secrets below are prerequisites.

## Design

### Why a hostname, not jithendar.com/harbor

Harbor cannot run under a path prefix. Its portal is built with `<base href="/">` and loads about 50 hashed
chunks, `/images/`, `/i18n/`, `/setting.json` and `/favicon.ico` from the root; its API, login and token service
are `/api/v2.0/`, `/c/`, `/service/token`; and Docker clients always use `/v2/` at the root of the registry host.
On jithendar.com those paths would collide with the website, and the registry's admin session would share an
origin with the site. Rewriting Harbor's responses in Traefik would break on every upgrade.

So Harbor gets its own hostname (as JFrog did), and `https://jithendar.com/harbor/…` redirects to
`https://registry.jithendar.com/harbor/…`, the portal's own routes. The hostname is a setting: change `externalURL`
and `expose.ingress.hosts.core` in `values.yaml` and the hostnames in `manifests/harbor-public-certificate.yaml`,
`manifests/harbor-http-ingress.yaml` and `manifests/harbor-shortcut-middleware.yaml`.

### Decisions

| Decision | Why |
|---|---|
| Harbor images built from source for arm64 and kept in the private Docker Hub repository `jithendar/custom-harbour` | goharbor publishes amd64 only (v2.15.4 on Docker Hub and GHCR); every mk4 node is arm64. One tag per component, `<component>-v2.15.4`. Docker Hub is reachable when Harbor is not, so Harbor never needs itself to start. |
| PostgreSQL 18 and Valkey 8 as StatefulSets beside the chart, not the chart's internal ones | Argo CD renders without cluster access. The chart's internal database takes its password only from values (default `changeit`), and its internal Redis has no password, yet Harbor keeps portal sessions in Redis. mk4 runs arbitrary code (MOKS terminals, the practice executor) and kindnet's NetworkPolicy enforcement is unproven, so both need real credentials. |
| Every credential in `harbor/harbor-credentials`, created by `./create-secrets.sh` | Anything the chart generates (`randAlphaNum`, `genCA`) would change on every Argo CD sync. The Redis password is spliced into each component's Redis URL at runtime (`$(REDIS_PASSWORD)`), because the chart's `existingSecret` for Redis needs Helm `lookup`. |
| Token-signing key from cert-manager (self-signed, 10 years, never rotated) | Same reason; cert-manager already owns TLS keys here. |
| Everything on `mk4-m05` | Volumes are minikube hostPath, which exist only on one node. m02–m04 already hold the other databases. |
| Chart ingress on Traefik, no Harbor nginx | Traefik is mk4's controller (Kong is on an unmerged branch). |
| Trivy and the metrics exporter off | Memory. The exporter image also hard-codes `GOARCH=amd64`. |

### Request path for push and pull

Client → Cloudflare DNS (**DNS only**) → host HAProxy (TCP, PROXY protocol, 1 h idle timeouts) → Traefik
`websecure` → Harbor core → registry → filesystem volume.

- **No Cloudflare proxy.** jithendar.com is served DNS-only (no `cf-ray` header). Proxied requests are capped
  at 100 MB on Cloudflare's Free and Pro plans, and Docker sends each layer as one request. Keep the new record
  grey-clouded.
- **Traefik read timeout.** Traefik v3 must read the whole request, body included, within
  `respondingTimeouts.readTimeout`, which defaults to 60s. `../traefik/values.yaml` raises it to 900s on
  `websecure`. That is a values change to the existing controller and takes `deploy-traefik.sh` to apply (see Prerequisites).
- **No body-size limit.** Traefik has none by default, and the chart's ingress-nginx annotations are removed.
- **Redirects.** With filesystem storage the registry serves blobs itself; nothing redirects to object storage.
  The token realm is `externalURL`, so clients fetch tokens from `https://registry.jithendar.com/service/token`.
- **From inside mk4.** A pod resolved jithendar.com to 46.224.38.14 and completed HTTPS to it (hairpin through
  the host) on 2026-11-06. Pulls by the nodes' containerd use the node's resolver and are untested: see Verify.

### Footprint

| Component | CPU request / limit | Memory request / limit | Storage |
|---|---|---|---|
| core | 100m / 1 | 256Mi / 512Mi | |
| jobservice (logs in PostgreSQL) | 50m / 500m | 128Mi / 512Mi | |
| portal | 10m / 200m | 32Mi / 128Mi | |
| registry + registryctl | 110m / 1.5 | 160Mi / 1.25Gi | `harbor-registry` 30Gi |
| PostgreSQL 18 | 100m / 1 | 256Mi / 1Gi | `harbor-postgres-data` 5Gi |
| Valkey 8 | 25m / 250m | 64Mi / 256Mi | `harbor-redis-data` 1Gi |
| **Total** | **395m / 4.45** | **896Mi / 3.6Gi** | **36Gi nominal** |
| Trivy, if enabled | 200m / 1 | 512Mi / 1Gi | 5Gi |
| Backup job, daily while it runs | 100m / 1 | 128Mi / 512Mi | |

The five nodes are containers on one 16 vCPU / 32 GB host, so these numbers come out of that one pool. On
2026-11-06 the nodes requested about 4.1 CPU and 8.2 GiB in total and used about 12.4 GiB. **Disk is the tight resource:**
hostPath sizes are not enforced, and the host filesystem every node shares had 43 GiB free of 150 GiB. Retention,
GC and a disk alert matter more than the nominal 30Gi. If blobs outgrow the disk, Harbor's `s3` storage pointed at
R2 is the next step; it is not configured here.

## Prerequisites

Each is a setting this repository did not have before.

1. **DNS.** In Cloudflare, an A record `registry.jithendar.com → 46.224.38.14`, **DNS only**. No AAAA record
   (jithendar.com has none). cert-manager's HTTP-01 challenge needs it before the first sync.
2. **Traefik timeout.** Apply `../traefik/values.yaml` with `./deploy-traefik.sh --context mk4`, or `helm upgrade`
   it some other way. The live release `traefik/traefik` is at revision 2 with status `failed`
   (`helm --kube-context mk4 -n traefik history traefik`); find out why before upgrading over it.
3. **Docker Hub.** Two access tokens: one with write access to `jithendar/custom-harbour` for the build, and a
   read-only one for mk4 to pull with. `deploy-harbor.sh` asks for the read-only one once and stores it as
   `harbor/dockerhub-pull`.
4. **Build machine.** A native arm64 Docker host with the Compose plugin that is **not** the mk4 server (the script
   refuses to build there). The workflow [`build-harbor-images.yml`](../../../.github/workflows/build-harbor-images.yml)
   runs the build on GitHub's hosted arm64 runner: add the repository secrets `DOCKERHUB_USERNAME` and
   `DOCKERHUB_TOKEN` (the write token). An Apple Silicon Mac with Docker Desktop, OrbStack or colima also works.
   The deploy step runs on any machine with kubectl access to mk4 and `htpasswd`, including the mk4 host.
5. **Node.** `mk4-m05`, set in `values.yaml` and `manifests/`. Change every `kubernetes.io/hostname` together,
   and only before the first sync: the volumes stay where the pods first ran.

## Deploy

After the prerequisites, merge this branch and push it: Argo CD reads `master`, and the script stops if your
Harbor files differ from `origin/master`. Then, from the repository root:

1. Build and push: GitHub, *Actions → Build Harbor images → Run workflow* (about half an hour the first time).
   On your own arm64 machine instead: `docker login`, then `bash deploy/minikube/harbor/deploy-harbor.sh --skip-deploy`.
2. Deploy, on the mk4 host or anywhere with kubectl access to mk4:

```bash
docker login                                   # lets the script check the images exist (it skips the check without docker)
python3 scripts/argocd/validate.py --external-charts
bash deploy/minikube/harbor/deploy-harbor.sh --skip-build
```

On an arm64 machine that is not the mk4 server, `bash deploy/minikube/harbor/deploy-harbor.sh` does both.

The script:

1. Builds Harbor v2.15.4 for arm64 from its pinned commit with Harbor's own Makefile.
2. Pushes `jithendar/custom-harbour:harbor-portal-v2.15.4`, `harbor-core-…`, `harbor-jobservice-…`,
   `registry-photon-…` and `harbor-registryctl-…`.
3. Runs `./create-secrets.sh --services harbor --apply` (namespace and `harbor-credentials`, never rotated), creates
   `dockerhub-pull` if it is missing, and runs `scripts/argocd/deploy.py harbor` (creates `mk-registry` and
   `harbor`, syncs without prune, waits for Healthy).

Afterwards, save a copy of `harbor-credentials` off-host (Backup and restore).

| To | Run |
|---|---|
| Build and push only, deploy later | `deploy-harbor.sh --skip-deploy` |
| Deploy images already pushed (also on re-syncs) | `deploy-harbor.sh --skip-build` |
| Include the Trivy adapter | `deploy-harbor.sh --with-trivy` |
| Another repository or cluster | `IMAGE_REPO=… KUBE_CONTEXT=…` (and change `values.yaml` to match) |
| Replace the pull token | `kubectl --context mk4 -n harbor delete secret dockerhub-pull`, then `--skip-build` |

`deploy.py` does not update an AppProject that already exists. After changing `project-registry.yaml`, run
`kubectl --context mk4 apply -f deploy/minikube/applications/optional/project-registry.yaml`.

First sign-in: `https://registry.jithendar.com/harbor/`, user `admin`. Read the password locally, never in CI:

```bash
kubectl --context mk4 -n harbor get secret harbor-credentials -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d; echo
```

## Verify

```bash
curl -sI https://jithendar.com/harbor | grep -i '^location'      # https://registry.jithendar.com/harbor
curl -sI https://registry.jithendar.com/v2/ | grep -iE '^HTTP|www-authenticate'
#   401 and: Bearer realm="https://registry.jithendar.com/service/token",service="harbor-registry"
```

**Push from CI** (GitHub-hosted runner, robot `robot$moks+ci` below). One layer over a minute's upload
exercises the read timeout and the absence of size limits:

```bash
echo "$HARBOR_ROBOT_SECRET" | docker login registry.jithendar.com -u 'robot$moks+ci' --password-stdin
printf 'FROM busybox:1.37\nRUN head -c 1500000000 /dev/urandom > /blob\n' | docker build -t registry.jithendar.com/jithendar/push-test:1 -
time docker push registry.jithendar.com/jithendar/push-test:1
```

**Pull through the node runtime** (kubelet and containerd, not a pod's network), with the read-only robot's Secret
in the namespace (Kubernetes pull access):

```bash
kubectl --context mk4 -n moks run harbor-pull-test --restart=Never --image=registry.jithendar.com/jithendar/push-test:1 \
  --overrides='{"spec":{"imagePullSecrets":[{"name":"harbor-pull"}],"nodeSelector":{"kubernetes.io/hostname":"mk4-m02"}}}' -- true
kubectl --context mk4 -n moks get pod harbor-pull-test -w      # Completed, not ErrImagePull
kubectl --context mk4 -n moks delete pod harbor-pull-test
```

Then delete `jithendar/push-test` in the portal.

## Projects and robot accounts

Every image the repositories build lives in **one private project, `jithendar`**, and is pushed and pulled as
`:latest` only:

```bash
docker tag SOURCE_IMAGE registry.jithendar.com/jithendar/REPOSITORY:latest
docker push registry.jithendar.com/jithendar/REPOSITORY:latest
```

| Repository | Images (`registry.jithendar.com/jithendar/…`) | CI robot |
|---|---|---|
| moks | `moks`, `moks-crewai`, `moks-reranker`, `moks-terminal`, `moks-opensearch` | `robot$jithendar+moks` |
| jithendar.com | `jithendar-com`, `practice-executor`, `practice-sandbox` | `robot$jithendar+jithendar-com` |
| roboshop-ai | `roboshop-<service>` (`roboshop-shipping` for shipping-service) | `robot$jithendar+roboshop-ai` |
| tools | none in Harbor: `jithendar/custom-harbour` stays on Docker Hub (Harbor cannot pull itself) | — |

[`registry.py`](registry.py) does the setup through Harbor's API, from a machine with kubectl access to mk4 (it reads
the admin password from `harbor-credentials`). No secret is printed or put on a command line:

```bash
python3 deploy/minikube/harbor/registry.py project        # private project "jithendar"
python3 deploy/minikube/harbor/registry.py ci-robots      # needs gh, signed in
python3 deploy/minikube/harbor/registry.py pull-secrets   # Secret harbor-pull in jithendar, moks, moks-terminals, moks-diagnostics, roboshop-ai
python3 deploy/minikube/harbor/registry.py copy-from-ecr  # optional, once: see Moving off ECR
```

- **CI robots** can push and pull in `jithendar` only. `ci-robots` sets `HARBOR_USERNAME` and `HARBOR_PASSWORD`
  as Actions secrets in each GitHub repository; the workflows log in with
  `printf '%s' "$HARBOR_PASSWORD" | docker login registry.jithendar.com -u "$HARBOR_USERNAME" --password-stdin`.
  They expire after 365 days and CI then fails at login: run `registry.py ci-robots --rotate`.
- **Kubernetes robot** `robot$k8s-pull` is system level, pull only, and never expires (an expired pull secret only
  shows up when a pod restarts). `pull-secrets` writes it as `harbor-pull` into each namespace; workloads name it in
  `imagePullSecrets`. Keep its secret in your password manager: a namespace created later
  (`registry.py pull-secrets --namespace NAME` asks for it) and a restore need it again. `--rotate` issues a new one
  and rewrites every namespace.

An existing robot keeps its secret unless `--rotate` is given, so every command is safe to rerun.

## Kubernetes pull access

Pods pull our images from Harbor only, through `imagePullSecrets: [{name: harbor-pull}]` in each workload (Git, so
Argo CD and `kubectl apply` keep it). Third-party images (postgres, OpenSearch, Valkey, Kafka, busybox) still come
from their own registries at pinned versions, and Harbor's own images from Docker Hub.

Every image runs `:latest` with `imagePullPolicy: Always`, so **while Harbor is down no pod that starts or restarts
can start**, even with the image cached on the node. Harbor itself is not affected: nothing it runs comes from Harbor.

### Moving off ECR

1. Harbor is up and `https://registry.jithendar.com` answers (Verify). Traefik's 900s read timeout is applied
   (Prerequisites): the MOKS images have layers over 1 GB.
2. `registry.py project`, `ci-robots`, `pull-secrets`.
3. Optional: `aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin
   881026659929.dkr.ecr.us-east-1.amazonaws.com`, `docker login registry.jithendar.com`, then `registry.py
   copy-from-ecr`. It copies each `:latest` (every platform), so pods can pull before CI has pushed.
   Otherwise run each repository's release workflow once before its manifests change.
4. Merge the `feat/harbor-registry` branch of each repository. The workflows push to Harbor; manifests pull from it.
5. Once no pod runs an ECR image (`kubectl --context mk4 get pods -A -o jsonpath='{..image}' | tr ' ' '\n' | grep dkr.ecr`
   prints nothing), retire `ecr-pull`: suspend `roboshop-ai/ecr-refresh` and delete the ECR repositories in
   Terraform (`infrastructure/eks_ecr.tf` in the tools repository applies on master, so review that plan on its own).

## Retention and garbage collection

CI pushes `:latest` only, and the cluster runs `:latest` with `imagePullPolicy: Always`. Each push leaves the build
it replaced untagged, and those are the rollback window: roll back by pointing `latest` at an earlier build's digest
(MOKS keeps them in its release records):
`docker buildx imagetools create --tag registry.jithendar.com/jithendar/IMAGE:latest registry.jithendar.com/jithendar/IMAGE@sha256:…`.
So the policy keeps the **last ten pushes of each repository, untagged ones included** (`"untagged": true`), and
garbage collection must not delete untagged artifacts itself (`delete_untagged: false`): retention decides. It runs
daily at 03:00 UTC. The calls below use this helper; the admin password goes to curl on stdin:

```bash
harbor() {   # harbor METHOD PATH [curl args...]
  local method=$1 path=$2; shift 2
  printf 'user = "admin:%s"\n' "$(kubectl --context mk4 -n harbor get secret harbor-credentials \
      -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)" |
    curl -fsS --config - -X "$method" -H 'Content-Type: application/json' \
      "https://registry.jithendar.com/api/v2.0$path" "$@"
}

id=$(harbor GET /projects/jithendar | python3 -c 'import json,sys; print(json.load(sys.stdin)["project_id"])')
repos='{"repository":[{"kind":"doublestar","decoration":"repoMatches","pattern":"**"}]}'
harbor POST /retentions -d "{\"algorithm\":\"or\",\"scope\":{\"level\":\"project\",\"ref\":$id},
  \"trigger\":{\"kind\":\"Schedule\",\"settings\":{\"cron\":\"0 0 3 * * *\"}},\"rules\":[
  {\"action\":\"retain\",\"template\":\"latestPushedK\",\"params\":{\"latestPushedK\":10},\"scope_selectors\":$repos,
   \"tag_selectors\":[{\"kind\":\"doublestar\",\"decoration\":\"matches\",\"pattern\":\"**\",\"extras\":\"{\\\"untagged\\\":true}\"}]}]}"
```

**Preview before the first scheduled run.** In the portal, the project's Policy tab has **DRY RUN**: it lists
what would be deleted and deletes nothing. The same through the API:

```bash
policy=$(harbor GET /projects/jithendar/metadatas/retention_id | python3 -c 'import json,sys; print(json.load(sys.stdin)["retention_id"])')
harbor POST "/retentions/$policy/executions" -d '{"dry_run":true}'
harbor GET "/retentions/$policy/executions"      # then .../executions/ID/tasks and .../tasks/ID for each log
```

**Garbage collection reclaims the disk.** Retention and deleting artifacts only remove references in the database;
blobs stay on the volume until GC removes every blob that no manifest references. Run it weekly, Saturday 04:00
UTC, outside the backup window (02:30). Preview it with `"dry_run":true` and `"type":"Manual"` first:

```bash
harbor POST /system/gc/schedule -d '{"schedule":{"type":"Manual"},"parameters":{"delete_untagged":false,"dry_run":true,"workers":1}}'
harbor GET /system/gc | head -c 600                 # status; the portal shows the log and the space it would free
harbor POST /system/gc/schedule -d '{"schedule":{"type":"Custom","cron":"0 0 4 * * 6"},"parameters":{"delete_untagged":false,"dry_run":false,"workers":1}}'
```

(A schedule that already exists is changed with `PUT` on the same path.)

## Trivy

Off by default (`trivy.enabled: false`). To enable: set `trivy.enabled: true` in `values.yaml`, merge, and run
`deploy-harbor.sh --with-trivy` (it downloads Trivy v0.75.0's `Linux-ARM64` build). It adds 200m / 512Mi requested, 1 CPU / 1Gi limit and a 5Gi volume, and
downloads its vulnerability database from the internet. Scans then run on push if a project enables
"Automatically scan images on push". CI scanning stays as it is: MOKS's `trivy-action` step scans the terminal
image before it is pushed and does not depend on the registry.

## Backup and restore

What a restore needs, and where it lives:

| Data | Backup |
|---|---|
| PostgreSQL `registry` (projects, robots, policies, artifact index) | `harbor-backup` CronJob: `pg_dump -Fc` to R2 `backups/harbor/postgres/` |
| Registry blobs (`/storage/docker`) | the same Job, `rclone copy` to R2 `backups/harbor/registry/` |
| `harbor-credentials` (secretKey decrypts stored credentials; the database password is baked into its data directory) | **by hand**, to your password manager or an encrypted file, never next to the data |
| `harbor-backup-r2`, robot secrets | by hand, as above |
| Redis, token key, TLS | not backed up: queues, sessions and keys are recreated |

Each night the Job dumps the database first, then copies blobs. R2 only ever gains blobs (copy, never sync), so
every dump finds all the blobs it references. `LATEST` names the newest dump whose blobs are complete. Keep GC
outside the window. Old dumps can expire with an R2 lifecycle rule on `backups/harbor/postgres/` (30 days); never
put one on `registry/`. To trim blobs that GC removed, run `rclone sync` once, right after a backup; from then on
only the newest dump is complete.

**Enable it.** Create an R2 API token for the bucket (Object Read & Write), then:

```bash
python3 - <<'EOF' | kubectl --context mk4 -n harbor apply -f -
import getpass, json
print(json.dumps({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque', 'metadata': {'name': 'harbor-backup-r2'},
                  'stringData': {'access-key-id': getpass.getpass('R2 access key id: '),
                                 'secret-access-key': getpass.getpass('R2 secret access key: '),
                                 'bucket': 'jithendar',
                                 'endpoint': 'https://<account id>.r2.cloudflarestorage.com'}}))
EOF
```

Then set `suspend: false` in `manifests/harbor-backup-cronjob.yaml`, merge, and sync. Run the first (full) copy by
hand rather than waiting for 02:30:
`kubectl --context mk4 -n harbor create job --from=cronjob/harbor-backup harbor-backup-first`.

Velero on mk4 is not a substitute: its server is not running, its last backup is from 2026-11-01, and its
schedule copies manifests only, no volume data.

**Back up the credentials** after creating them, and again only if they change:

```bash
kubectl --context mk4 -n harbor get secret harbor-credentials -o json |
  python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({k: s[k] for k in ("apiVersion","kind","type","data")} | {"metadata": {"name": s["metadata"]["name"], "namespace": "harbor"}}))' > harbor-credentials.json
# store harbor-credentials.json in the password manager, then: rm harbor-credentials.json
```

### Restore, including after Minikube is rebuilt

Harbor's own images come from `jithendar/custom-harbour` and the database and Redis images from public Docker Hub,
so none of this needs Harbor.

1. Rebuild the cluster as usual: `./deploy-minikube.sh`, `./bootstrap-cluster.sh --context mk4` (cert-manager,
   Traefik, Argo CD, ECR pull secrets), and re-apply the Traefik timeout (Prerequisites).
2. `kubectl --context mk4 apply -f deploy/minikube/namespaces/harbor.yaml`, then apply the saved
   `harbor-credentials.json` and recreate `harbor-backup-r2`. Restore `harbor-credentials` **before** anything
   else: `create-secrets.sh` would otherwise generate a new one, which cannot decrypt the restored data.
3. `bash deploy/minikube/harbor/deploy-harbor.sh --skip-build`. It reuses the restored Secret, recreates
   `dockerhub-pull`, and syncs. Harbor starts empty; core creates a fresh schema.
5. Stop everything that writes, then restore the dump named in `LATEST` and the blobs:
   ```bash
   kubectl --context mk4 -n harbor scale deploy harbor-core harbor-jobservice harbor-registry --replicas=0
   sed 's/DUMP_NAME/registry-20261107T023000Z.dump/' deploy/minikube/harbor/examples/harbor-restore-job.yaml |
     kubectl --context mk4 apply -f -
   kubectl --context mk4 -n harbor wait --for=condition=Complete job/harbor-restore --timeout=6h
   kubectl --context mk4 -n harbor delete job harbor-restore
   python3 scripts/argocd/deploy.py harbor --context mk4       # restores the replicas
   ```
6. Verify (above) and sign in with the old admin password. Robots and their secrets come back with the database.
   Recreate `harbor-pull` in namespaces that were rebuilt, from the `robot$k8s-pull` secret in your password manager.

Until Harbor is back, workloads whose images live only in Harbor cannot start on a new node. For the pilot, keep
pushing critical images to ECR too.

## Pilot

1. Deploy, verify, and create the projects and robots.
2. Push one non-critical image to both ECR and Harbor from its CI for a week. A candidate is MOKS's diagnostic
   `terminal` image; that repository's workflow has to change.
3. Switch its workload to `registry.jithendar.com/moks/…` with `harbor-pull`, watch pulls, retention dry runs,
   one GC and one restore drill into a scratch namespace.
4. Move the rest project by project, then delete their ECR repositories.

## Roll back

Harbor is additive: no existing workload uses it until its manifests change. To stop it, delete the Application
**without cascading** (`kubectl --context mk4 -n argocd delete application harbor`; `Delete=false` and the PVC
annotations keep the volumes). Never sync it with prune. To remove the data too, delete the `harbor` namespace
after a final backup. Reverting the Traefik timeout is a values change and a Traefik upgrade.

## Not verified

- No Harbor runs anywhere yet: render, `validate.py` and a server-side dry run on mk4 (every object accepted,
  nothing created) are the only checks.
- `deploy-harbor.sh` has not run. Its build path follows Harbor's Makefile and release workflow, and nothing in the
  components it builds pins amd64, but no arm64 build of v2.15.4 has been seen to succeed.
- Push and pull through Traefik with the 900s timeout, node-runtime pulls, retention, GC, backup and restore all
  depend on a running Harbor.
- Harbor's support for this exact external PostgreSQL 18 / Valkey 8 combination is inferred: its bundled database
  for v2.15 is PostgreSQL 18 and its bundled Redis is Valkey.
