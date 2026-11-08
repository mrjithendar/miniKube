# Host: build cache and unused images (running minikube nodes keep their kicbase image)
docker builder prune -af
docker image prune -af

# Inside every mk4 node: remove images no pod is using
for n in $(docker ps --format '{{.Names}}' | grep -E '^mk4(-m[0-9]+)?$'); do
  echo "== $n"; docker exec "$n" crictl rmi --prune
done

# Logs and package cache
journalctl --vacuum-size=500M
dnf clean all

df -h /


docker system df
du -xh --max-depth=2 /var/lib 2>/dev/null | sort -rh | head -20
du -sh /var/lib/docker/volumes/* 2>/dev/null | sort -rh | head
journalctl --disk-usage
