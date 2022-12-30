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
