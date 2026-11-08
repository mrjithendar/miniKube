centOS 7

sudo adduser minikube

sudo passwd minikube

usermod -aG wheel minikube 

su minikube

cd $home

sudo yum install docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker

systemctl restart docker

sudo usermod -aG docker $USER && newgrp docker

curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

minikube start

# install KUBECTL
https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/

kubectl proxy --address='0.0.0.0' --disable-filter=true --accept-hosts='^*$'

Login: http://public_ip:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/#/login

# Create service account
kubectl create serviceaccount -n kube-system cluster-admin-dashboard-sa

# Bind ClusterAdmin role to the service account
kubectl create clusterrolebinding -n kube-system cluster-admin-dashboard-sa \
  --clusterrole=cluster-admin \
  --serviceaccount=kube-system:cluster-admin-dashboard-sa

# Parse the token
kubectl get secret $(kubectl get serviceaccount -n kube-system cluster-admin-dashboard-sa -o jsonpath="{.secrets[0].name}") -o jsonpath="{.data.token}" | base64 --decode


