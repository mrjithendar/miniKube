#!/bin/sh
# Helm post-renderer for the Valkey release (minikube-infrastructure/redis.tf): the chart's master-proxy HAProxy has
# "timeout client/server 30s" and no tunnel timeout, so a connection with no traffic for 30 s is closed: idle Pub/Sub
# subscribers and blocking reads (BRPOP queues) would be cut over and over. "timeout tunnel" governs an established TCP
# connection in both directions; 1h matches Envoy's TCP idle timeout. Exits non-zero (and the apply fails) when the
# expected line is missing, e.g. after a chart upgrade changed the config.
set -eu
awk '
  { print }
  /^[[:space:]]*timeout server 30s[[:space:]]*$/ {
    match($0, /^[[:space:]]*/)
    printf "%stimeout tunnel 1h\n", substr($0, 1, RLENGTH)
    n++
  }
  END { if (n == 0) { print "postrender-haproxy.sh: \"timeout server 30s\" not found in the rendered chart" > "/dev/stderr"; exit 1 } }
'
