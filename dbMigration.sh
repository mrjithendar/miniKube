#!/usr/bin/env bash
# Moves each application's database from its own PostgreSQL pod on mk4 into the shared CloudNativePG cluster
# postgres-db/postgres-cluster (miniKube/minikube-infrastructure/postgres.tf, README "PostgreSQL: Moving an application").
#
#   source pod                              -> target database / owning role (Terraform var.postgres_databases)
#   jithendar/jithendar-postgres-0 (PG 18)  -> jithendar
#   moks/moks-postgres-0           (PG 17)  -> moks
#   harbor/harbor-postgres-0       (PG 18)  -> registry
#   roboshop-ai/deploy/shipping-db (PG 18)  -> shipping
#
# How: mk4's API server is reached over the internet and long `kubectl exec` streams are cut after a few seconds, so
# nothing large goes through kubectl. The copy runs INSIDE the source pod, detached:
#   pg_dump -Fc (local socket) | pg_restore --no-owner --no-privileges --single-transaction
#     -h postgres-cluster-rw.postgres-db.svc  -U <role>  -d <role>
# and this script only polls it with short execs. --single-transaction: a failed restore leaves the target empty.
# Everything restored is owned by the tenant role; GRANTs to extra source roles (moks_api, shipping_app, ...) are not
# copied: on the new cluster each application logs in as its one role (Secret <ns>/postgres-db-credentials).
#
# Per database:
#   1. preflight: source pod, target primary, target role Secret, target database exists and is EMPTY (--force empties it)
#   2. if the source pod cannot reach postgres-db:5432 (jithendar, moks restrict egress), a temporary NetworkPolicy
#      "db-migration-egress" lets just that pod out; it is deleted again at the end
#   3. stop writes: pause Argo CD auto-sync of the owning Applications (selfHeal would scale them back), scale the
#      writer Deployments to 0. Saved in ./db-migration-state/<db>/ for --start-writers
#   4. copy, ANALYZE, compare the exact row count of every table on both sides
#   5. success: writers STAY STOPPED (restarting them on the old database would make the two diverge). Point the
#      application at postgres-db-credentials, then run --start-writers <db>.
#      failure: the old database is untouched; writers and Argo CD are restored automatically.
#
# Usage:
#   ./dbMigration.sh --check                     read-only preflight for all databases
#   ./dbMigration.sh [options] [db ...]          migrate (default: jithendar moks registry shipping)
#   ./dbMigration.sh --start-writers db ...      scale writers back and re-enable Argo CD auto-sync
# Options:
#   --yes        do not ask for confirmation
#   --force      target database not empty: DROP OWNED BY <role> in it first (destroys what is there)
#   --no-stop    do not stop the writers (copy is a consistent snapshot, but later writes are not copied)
# Environment: KUBE_CONTEXT (default mk4), JOB_TIMEOUT seconds per database (default 3600).

set -Eeuo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-mk4}"
JOB_TIMEOUT="${JOB_TIMEOUT:-3600}"
TARGET_NS=postgres-db
TARGET_CLUSTER=postgres-cluster
TARGET_HOST="${TARGET_CLUSTER}-rw.${TARGET_NS}.svc.cluster.local"
ARGO_NS=argocd
TEMP_NP="db-migration-egress"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/db-migration-state"
ALL_DBS="jithendar moks registry shipping"

K() { kubectl --context "$KUBE_CONTEXT" "$@"; }

if [ -t 1 ]; then R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' B=$'\e[1m' N=$'\e[0m'; else R='' G='' Y='' B='' N=''; fi
log()  { printf '%s[%s]%s %s\n' "$B" "$(date +%H:%M:%S)" "$N" "$*"; }
ok()   { printf '  %sok%s   %s\n' "$G" "$N" "$*"; }
warn() { printf '  %swarn%s %s\n' "$Y" "$N" "$*" >&2; }
err()  { printf '  %sFAIL%s %s\n' "$R" "$N" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---------------------------------------------------------------------------------------------------------------------
# Per-database configuration (bash 3.2 on macOS: no associative arrays).
#   SRC_NS SRC_POD SRC_CONTAINER   where the old PostgreSQL runs (image env POSTGRES_USER / POSTGRES_DB)
#   SRC_SEL_KEY SRC_SEL_VAL        its pod label, for the temporary egress NetworkPolicy
#   WRITERS                        Deployments in SRC_NS that use the database (scaled to 0 during the copy)
#   ARGO_APPS                      Argo CD Applications with selfHeal over those Deployments; parents first
# ---------------------------------------------------------------------------------------------------------------------
db_config() {
  case "$1" in
    jithendar)
      SRC_NS=jithendar SRC_POD=pod/jithendar-postgres-0 SRC_CONTAINER=postgres
      SRC_SEL_KEY=app SRC_SEL_VAL=jithendar-postgres
      WRITERS="jithendar-com jithendar-db-ui practice-dispatcher practice-executor-api"
      ARGO_APPS="" ;;
    moks)
      SRC_NS=moks SRC_POD=pod/moks-postgres-0 SRC_CONTAINER=postgres
      SRC_SEL_KEY=app SRC_SEL_VAL=moks-postgres
      WRITERS="moks-api moks-crewai moks-executor moks-terminal moks-worker"
      ARGO_APPS="moks-root moks-api moks-crewai moks-executor moks-terminal moks-worker" ;;
    registry)
      SRC_NS=harbor SRC_POD=pod/harbor-postgres-0 SRC_CONTAINER=postgres
      SRC_SEL_KEY=app.kubernetes.io/name SRC_SEL_VAL=harbor-postgres
      WRITERS="harbor-core harbor-jobservice"
      ARGO_APPS="harbor" ;;
    shipping)
      SRC_NS=roboshop-ai SRC_POD=deploy/shipping-db SRC_CONTAINER=shipping-db
      SRC_SEL_KEY=app SRC_SEL_VAL=shipping-db
      WRITERS="shipping-service"
      ARGO_APPS="roboshop-shipping-service" ;;
    *) die "unknown database '$1' (known: $ALL_DBS)" ;;
  esac
}

# Short commands only: anything long-running is detached inside the pod.
src_exec()  { K -n "$SRC_NS" exec "$SRC_POD" -c "$SRC_CONTAINER" -- sh -c "$@"; }
# Writes a file in the source pod from an ARGUMENT, not stdin: on mk4's remote API server `kubectl exec -i` delivers no
# stdin (the file comes out empty). Checks the size that arrived. Content is visible in the exec request (TLS to the API
# server) and briefly in the pod's process list.
src_put() { # path content
  local b64 want got
  b64="$(printf '%s' "$2" | base64 | tr -d '\n')"
  want="$(printf '%s' "$2" | wc -c | tr -d ' ')"
  got="$(src_exec 'umask 077; printf %s "$2" | base64 -d > "$1" && wc -c < "$1"' sh "$1" "$b64" | tr -d ' \r')"
  [ "$got" = "$want" ] || { err "writing $1 in the pod: got ${got:-nothing} bytes, expected $want"; return 1; }
}
src_psql()  { src_exec 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' sh "$1"; }
tgt_psql()  { K -n "$TARGET_NS" exec "$PRIMARY" -c postgres -- psql -X -v ON_ERROR_STOP=1 -d "$1" -Atc "$2"; }

# Exact row count of every table, "schema.table|rows", sorted. Superuser on both sides, so every table is visible.
COUNT_SQL="select format('%s.%s|%s', table_schema, table_name,
  (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text)
from information_schema.tables
where table_type = 'BASE TABLE' and table_schema not in ('pg_catalog', 'information_schema')
order by 1"

# Objects in the target database outside the system schemas, not counting extension members (CloudNativePG creates
# pg_stat_statements in every database). An empty tenant database has none.
OBJECTS_SQL="select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_(toast|temp)'
  and c.relkind in ('r','p','v','m','S','f')
  and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')"

primary() {
  PRIMARY="$(K -n "$TARGET_NS" get cluster.postgresql.cnpg.io "$TARGET_CLUSTER" -o jsonpath='{.status.currentPrimary}')"
  [ -n "$PRIMARY" ] || die "cluster $TARGET_NS/$TARGET_CLUSTER has no current primary"
  local phase
  phase="$(K -n "$TARGET_NS" get cluster.postgresql.cnpg.io "$TARGET_CLUSTER" -o jsonpath='{.status.phase}')"
  [ "$phase" = "Cluster in healthy state" ] || warn "cluster phase: $phase"
}

can_reach_target() { src_exec "pg_isready -q -t 5 -h $TARGET_HOST -p 5432" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------------------------------------------------
# Preflight (read-only). Returns non-zero when the migration of this database cannot start.
# ---------------------------------------------------------------------------------------------------------------------
preflight() {
  local db="$1" bad=0 v
  db_config "$db"
  log "$B$db$N  ($SRC_NS/${SRC_POD#*/} -> $TARGET_NS/$TARGET_CLUSTER database $db)"

  if v="$(src_psql 'select version()' 2>&1)"; then
    ok "source: ${v%% on *}, $(src_psql 'select pg_size_pretty(pg_database_size(current_database()))'), $(src_psql "select count(*) from information_schema.tables where table_type='BASE TABLE' and table_schema not in ('pg_catalog','information_schema')") tables"
  else
    err "source not reachable: $v"; return 1
  fi

  if K -n "$TARGET_NS" get secret "db-$db" >/dev/null 2>&1; then ok "target role secret $TARGET_NS/db-$db"
  else err "no Secret $TARGET_NS/db-$db (add $db to postgres_databases and apply Terraform)"; bad=1; fi

  if [ "$(tgt_psql postgres "select count(*) from pg_database where datname = '$db'")" = 1 ]; then
    v="$(tgt_psql "$db" "$OBJECTS_SQL")"
    if [ "$v" = 0 ]; then ok "target database $db exists and is empty"
    elif [ "$FORCE" = 1 ]; then warn "target database $db has $v objects: --force will DROP OWNED BY $db"
    else err "target database $db already has $v objects (re-run with --force to empty it)"; bad=1; fi
  else
    err "target database $db does not exist"; bad=1
  fi

  if can_reach_target; then ok "source pod reaches $TARGET_HOST:5432"
  else warn "source pod cannot reach $TARGET_HOST:5432: a temporary NetworkPolicy $SRC_NS/$TEMP_NP will be added"; fi

  local w r
  for w in $WRITERS; do
    r="$(K -n "$SRC_NS" get deploy "$w" -o jsonpath='{.spec.replicas}' 2>/dev/null)" || { warn "writer deploy/$w not found (skipped)"; continue; }
    ok "writer deploy/$w replicas=$r"
  done
  for w in $ARGO_APPS; do
    r="$(K -n "$ARGO_NS" get application "$w" -o jsonpath='{.spec.syncPolicy.automated}' 2>/dev/null)" || { warn "Argo CD application $w not found"; continue; }
    ok "Argo CD application $w automated=${r:-off}"
  done
  [ -f "$STATE_DIR/$db/replicas" ] && warn "saved writer state exists in $STATE_DIR/$db (writers may still be stopped from an earlier run)"
  return $bad
}

# ---------------------------------------------------------------------------------------------------------------------
# Writers and Argo CD
# ---------------------------------------------------------------------------------------------------------------------
stop_writers() {
  local db="$1"; local app w r dir="$STATE_DIR/$db"
  mkdir -p "$dir"
  for app in $ARGO_APPS; do
    [ -f "$dir/argo-$app.json" ] && continue      # already paused by an earlier run: keep the original policy
    K -n "$ARGO_NS" get application "$app" -o jsonpath='{.spec.syncPolicy}' >"$dir/argo-$app.json" 2>/dev/null || continue
    K -n "$ARGO_NS" patch application "$app" --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}' >/dev/null
    ok "paused Argo CD auto-sync of $app"
  done
  touch "$dir/replicas"
  for w in $WRITERS; do
    r="$(K -n "$SRC_NS" get deploy "$w" -o jsonpath='{.spec.replicas}' 2>/dev/null)" || continue
    grep -q "^$w " "$dir/replicas" || echo "$w $r" >>"$dir/replicas"
    K -n "$SRC_NS" scale deploy "$w" --replicas=0 >/dev/null
  done
  local n
  for _ in $(seq 1 60); do
    n=0
    for w in $WRITERS; do
      r="$(K -n "$SRC_NS" get deploy "$w" -o jsonpath='{.status.replicas}' 2>/dev/null || true)"
      [ -n "$r" ] && [ "$r" != 0 ] && n=$((n + r))
    done
    [ "$n" = 0 ] && { ok "writers stopped: $WRITERS"; return 0; }
    sleep 3
  done
  err "writers still have $n pods after 180s"; return 1
}

start_writers() {
  local db="$1"; local dir="$STATE_DIR/$db" w r app apps="" policy
  db_config "$db"
  [ -f "$dir/replicas" ] || { warn "$db: no saved state in $dir, nothing to start"; return 0; }
  log "$db: starting writers"
  while read -r w r; do
    [ -n "$w" ] || continue
    K -n "$SRC_NS" scale deploy "$w" --replicas="$r" >/dev/null && ok "deploy/$w replicas=$r"
  done <"$dir/replicas"
  for app in $ARGO_APPS; do apps="$app $apps"; done   # children first, parent (moks-root) last
  for app in $apps; do
    [ -f "$dir/argo-$app.json" ] || continue
    policy="$(cat "$dir/argo-$app.json")"
    if [ -n "$policy" ]; then
      K -n "$ARGO_NS" patch application "$app" --type merge -p "{\"spec\":{\"syncPolicy\":$policy}}" >/dev/null
      ok "restored Argo CD sync policy of $app"
    fi
    rm -f "$dir/argo-$app.json"
  done
  rm -f "$dir/replicas"; rmdir "$dir" 2>/dev/null || true
}

# ---------------------------------------------------------------------------------------------------------------------
# Temporary egress for the source pod
# ---------------------------------------------------------------------------------------------------------------------
open_egress() {
  can_reach_target && return 0
  K apply -f - >/dev/null <<EOF || { err "could not create $SRC_NS/$TEMP_NP"; return 1; }
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: $TEMP_NP
  namespace: $SRC_NS
  labels:
    app.kubernetes.io/managed-by: dbMigration.sh
spec:
  podSelector:
    matchLabels:
      "$SRC_SEL_KEY": "$SRC_SEL_VAL"
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: $TARGET_NS
      ports:
        - { port: 5432, protocol: TCP }
    - ports:
        - { port: 53, protocol: UDP }
        - { port: 53, protocol: TCP }
EOF
  EGRESS_NS="$SRC_NS"
  for _ in $(seq 1 12); do can_reach_target && { ok "temporary NetworkPolicy $SRC_NS/$TEMP_NP: target reachable"; return 0; }; sleep 5; done
  err "source pod still cannot reach $TARGET_HOST:5432 with $SRC_NS/$TEMP_NP (Istio Sidecar egress list? NetworkPolicy on $TARGET_NS?)"; return 1
}

close_egress() {
  [ -n "${EGRESS_NS:-}" ] || return 0
  K -n "$EGRESS_NS" delete networkpolicy "$TEMP_NP" --ignore-not-found >/dev/null 2>&1 && ok "removed $EGRESS_NS/$TEMP_NP"
  EGRESS_NS=
}

# ---------------------------------------------------------------------------------------------------------------------
# The copy, detached inside the source pod
# ---------------------------------------------------------------------------------------------------------------------
run_copy() {
  local db="$1"; local job="/tmp/dbmigrate-$db" pw esc rc="" start elapsed dead=0
  pw="$(K -n "$TARGET_NS" get secret "db-$db" -o jsonpath='{.data.password}' | base64 -d)"
  esc="$(printf '%s' "$pw" | sed 's/[\\:]/\\&/g')"

  # Password into a 0600 file the job deletes when it ends.
  src_put "$job.pgpass" "$(printf '%s:5432:%s:%s:%s' "$TARGET_HOST" "$db" "$db" "$esc")
" || return 1
  src_put "$job.sh" "$(cat <<EOF
#!/bin/sh
set -o pipefail
export PGPASSFILE=$job.pgpass
pg_dump -U "\$POSTGRES_USER" -d "\$POSTGRES_DB" -Fc --verbose \\
  | pg_restore -h $TARGET_HOST -p 5432 -U $db -d $db \\
      --no-owner --no-privileges --single-transaction --exit-on-error --verbose
rc=\$?
rm -f $job.pgpass
echo \$rc > $job.rc
EOF
)
" || return 1
  src_exec "rm -f $job.rc $job.log $job.pid; if command -v setsid >/dev/null; then d=setsid; else d=; fi; \
            \$d nohup sh $job.sh > $job.log 2>&1 < /dev/null & echo \$! > $job.pid" >/dev/null || { err "could not start the job"; return 1; }
  ok "copy started in $SRC_NS/${SRC_POD#*/} (log $job.log in the pod)"

  start=$(date +%s)
  while :; do
    sleep 5
    # "<rc>" when finished, "running" while the job's process exists, "dead" if it vanished without a result.
    rc="$(src_exec "if [ -s $job.rc ]; then cat $job.rc; elif kill -0 \$(cat $job.pid 2>/dev/null) 2>/dev/null; then echo running; else echo dead; fi" 2>/dev/null || echo unknown)"
    case "$rc" in
      running|unknown) ;;
      dead) dead=$((dead + 1)); [ "$dead" -ge 2 ] && { err "the job in the pod stopped without a result"; break; } ;;
      *) break ;;
    esac
    elapsed=$(( $(date +%s) - start ))
    [ "$elapsed" -lt "$JOB_TIMEOUT" ] || { err "copy did not finish in ${JOB_TIMEOUT}s"; rc=timeout; break; }
    printf '\r  ...  %4ss  %-90.90s' "$elapsed" "$(src_exec "tail -n 1 $job.log" 2>/dev/null || true)"
  done
  printf '\r%*s\r' 110 ''
  if [ "$rc" != 0 ]; then
    err "pg_dump | pg_restore exited $rc; last lines of $job.log:"
    src_exec "grep -Ev ': (processing|creating|launching|executing|dumping|reading|saving|identifying|finding|flagging|started|reading)' $job.log 2>/dev/null | tail -n 25; rm -f $job.pgpass $job.pid" >&2 || true
    return 1
  fi
  ok "copy finished in $(( $(date +%s) - start ))s"
  src_exec "rm -f $job.sh $job.pgpass $job.pid" || true
}

verify() {
  local db="$1"; local dir="$STATE_DIR/$db" src tgt
  tgt_psql "$db" 'ANALYZE' >/dev/null
  src="$(src_psql "$COUNT_SQL")"
  tgt="$(tgt_psql "$db" "$COUNT_SQL")"
  mkdir -p "$dir"; printf '%s\n' "$src" >"$dir/rows.source"; printf '%s\n' "$tgt" >"$dir/rows.target"
  if [ "$src" = "$tgt" ]; then
    ok "row counts match: $(printf '%s\n' "$src" | grep -c .) tables, $(printf '%s\n' "$src" | awk -F'|' '{s+=$2} END {print s+0}') rows"
    return 0
  fi
  err "row counts differ (source < > target):"
  diff "$dir/rows.source" "$dir/rows.target" | grep '^[<>]' | head -n 30 >&2 || true
  [ "$STOP" = 1 ] && return 1
  warn "--no-stop: writers kept running, differences are expected"; return 0
}

migrate() {
  local db="$1"
  db_config "$db"
  log "$B$db$N: migrating"
  # Called from `if migrate`: set -e is off in here, every step is checked explicitly.
  if [ "$FORCE" = 1 ] && [ "$(tgt_psql "$db" "$OBJECTS_SQL")" != 0 ]; then
    tgt_psql "$db" "DROP OWNED BY \"$db\"" >/dev/null || { err "DROP OWNED BY $db failed"; return 1; }
    ok "emptied target database $db (DROP OWNED BY $db)"
  fi
  open_egress || { close_egress; return 1; }
  if [ "$STOP" = 1 ]; then
    stop_writers "$db" || { close_egress; start_writers "$db"; return 1; }
  else
    warn "--no-stop: writers keep running"
  fi

  local others
  others="$(src_psql "select count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() and backend_type = 'client backend'")"
  [ "$others" = 0 ] || warn "$others other client connection(s) on the source database (the dump is still one consistent snapshot)"

  if run_copy "$db" && verify "$db"; then
    close_egress
    log "${G}$db done${N}. Writers stay stopped. Next: point them at $SRC_NS/postgres-db-credentials, then"
    echo "      $0 --start-writers $db"
    return 0
  fi
  close_egress
  err "$db failed; old database untouched. Target left as is (re-run with --force to retry)."
  [ "$STOP" = 1 ] && start_writers "$db"
  return 1
}

# ---------------------------------------------------------------------------------------------------------------------
MODE=migrate FORCE=0 STOP=1 YES=0 DBS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --start-writers) MODE=start ;;
    --force) FORCE=1 ;;
    --no-stop) STOP=0 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,/^set -E/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 (--help)" ;;
    *) db_config "$1"; DBS="$DBS $1" ;;
  esac
  shift
done
[ -n "$DBS" ] || { [ "$MODE" = start ] && die "--start-writers needs database names ($ALL_DBS)"; DBS="$ALL_DBS"; }

command -v kubectl >/dev/null || die "kubectl not found"
K get namespace "$TARGET_NS" >/dev/null || die "cannot reach namespace $TARGET_NS with context $KUBE_CONTEXT"

if [ "$MODE" = start ]; then
  for db in $DBS; do start_writers "$db"; done
  exit 0
fi

primary
log "context $KUBE_CONTEXT, target $TARGET_NS/$TARGET_CLUSTER (primary $PRIMARY)"
failed=""
for db in $DBS; do preflight "$db" || failed="$failed $db"; done
[ "$MODE" = check ] && { [ -z "$failed" ] && log "${G}preflight passed${N}" || log "${R}preflight failed:${N}$failed"; [ -z "$failed" ]; exit $?; }
[ -z "$failed" ] || die "preflight failed for:$failed"

if [ "$YES" != 1 ]; then
  [ -t 0 ] || die "not a terminal: pass --yes to confirm"
  echo
  [ "$STOP" = 1 ] && echo "This STOPS the writers of:$DBS (and pauses their Argo CD auto-sync) until you run --start-writers."
  printf 'Migrate%s into %s/%s? [y/N] ' "$DBS" "$TARGET_NS" "$TARGET_CLUSTER"
  read -r answer; case "$answer" in y|Y|yes) ;; *) die "aborted" ;; esac
fi

EGRESS_NS=
trap 'close_egress' EXIT
trap 'echo; err "interrupted: the copy may still run in the source pod; saved writer state in $STATE_DIR"; exit 130' INT TERM

done_dbs="" failed=""
for db in $DBS; do
  if migrate "$db"; then done_dbs="$done_dbs $db"; else failed="$failed $db"; fi
done
echo
log "migrated:${done_dbs:- none}"
[ -z "$failed" ] || { log "${R}failed:${N}$failed"; exit 1; }
