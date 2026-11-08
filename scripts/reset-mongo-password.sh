#!/usr/bin/env bash
CTX=mk4; NS=roboshop-ai; NODE=mk4-m03
kubectl --context $CTX -n $NS get pod -l app=mongodb -o jsonpath='{.items[0].spec.nodeName}{"\n"}'   # must print mk4-m03

kubectl --context $CTX -n $NS scale deploy/mongodb --replicas=0
kubectl --context $CTX -n $NS wait --for=delete pod -l app=mongodb --timeout=120s


cat <<EOF | kubectl --context $CTX -n $NS apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: mongodb-reset
spec:
  nodeName: $NODE
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
  - name: mongod
    image: mongo:8.2
    args: [mongod, --noauth, --bind_ip, 127.0.0.1]
    env:
    - name: MONGO_ROOT_PASSWORD
      valueFrom: {secretKeyRef: {name: roboshop-secrets, key: MONGO_ROOT_PASSWORD}}
    - name: MONGO_APP_PASSWORD
      valueFrom: {secretKeyRef: {name: roboshop-secrets, key: MONGO_APP_PASSWORD}}
    volumeMounts: [{name: data, mountPath: /data/db}]
  volumes:
  - name: data
    persistentVolumeClaim: {claimName: mongodb-data}
EOF
until kubectl --context $CTX -n $NS exec mongodb-reset -- mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' 2>/dev/null | grep -q 1; do sleep 2; done; echo ready


kubectl --context $CTX -n $NS exec mongodb-reset -- mongosh --quiet --eval '
  print("roboshop collections:", db.getSiblingDB("roboshop").getCollectionNames().length);
  print("users:", JSON.stringify(db.getSiblingDB("admin").system.users.find({}, {user:1, db:1, _id:0}).toArray()))'
kubectl --context $CTX -n $NS exec mongodb-reset -- mongodump --quiet --archive --gzip > mongodb-backup-$(date +%Y%m%d-%H%M).archive.gz
ls -lh mongodb-backup-*.archive.gz


kubectl --context $CTX -n $NS exec mongodb-reset -- mongosh --quiet --eval '
  const admin = db.getSiblingDB("admin"), app = db.getSiblingDB("roboshop");
  admin.getUser("root") ? admin.changeUserPassword("root", process.env.MONGO_ROOT_PASSWORD)
                        : admin.createUser({user: "root", pwd: process.env.MONGO_ROOT_PASSWORD, roles: ["root"]});
  app.getUser("roboshop") ? app.changeUserPassword("roboshop", process.env.MONGO_APP_PASSWORD)
                          : app.createUser({user: "roboshop", pwd: process.env.MONGO_APP_PASSWORD, roles: [{role: "readWrite", db: "roboshop"}]});
  print("passwords updated")'


kubectl --context $CTX -n $NS exec mongodb-reset -- mongosh --quiet admin --eval 'db.shutdownServer()' || true   # a "connection closed" error is expected
kubectl --context $CTX -n $NS wait --for=jsonpath='{.status.phase}'=Succeeded pod/mongodb-reset --timeout=120s
kubectl --context $CTX -n $NS delete pod mongodb-reset


kubectl --context $CTX -n $NS patch deploy/mongodb --type=merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"mk4-m03"}}}}}'
kubectl --context $CTX -n $NS scale deploy/mongodb --replicas=1
kubectl --context $CTX -n $NS rollout status deploy/mongodb --timeout=5m


kubectl --context $CTX -n $NS exec deploy/mongodb -- sh -c \
  'mongosh --quiet -u roboshop -p "$MONGO_APP_PASSWORD" --authenticationDatabase roboshop roboshop --eval "db.runCommand({ping:1}).ok"'   # prints 1
kubectl --context $CTX -n $NS delete pod -l 'app in (ai-gateway,user-service,catalogue-service)'
kubectl --context $CTX -n $NS get pods -w
