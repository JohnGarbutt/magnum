#!/bin/bash
#
# These instructions assume an Ubuntu-based host or VM for running devstack.
# Please note that if you are running this in a VM, it is vitally important
# that the underlying hardware have nested virtualization enabled or you will
# experience very poor amphora performance.
#
# Heavily based on:
# https://opendev.org/openstack/octavia/src/branch/master/devstack/contrib/new-octavia-devstack.sh

set -ex

# Set up the packages we need. Ubuntu package manager is assumed.
sudo apt-get update
sudo apt-get install git vim -y

# Clone the devstack repo
sudo mkdir -p /opt/stack
if [ ! -f /opt/stack/stack.sh ]; then
    sudo chown -R ${USER}. /opt/stack
    git clone https://git.openstack.org/openstack-dev/devstack /opt/stack
fi

cat <<EOF > /opt/stack/local.conf
[[local|localrc]]
enable_plugin magnum https://opendev.org/openstack/magnum
enable_plugin magnum-ui https://opendev.org/openstack/magnum-ui
enable_plugin heat https://opendev.org/openstack/heat
enable_plugin barbican https://opendev.org/openstack/barbican
enable_plugin octavia https://opendev.org/openstack/octavia
LIBS_FROM_GIT+=python-octaviaclient

DATABASE_PASSWORD=secretdatabase
RABBIT_PASSWORD=secretrabbit
ADMIN_PASSWORD=secretadmin
SERVICE_PASSWORD=secretservice
SERVICE_TOKEN=111222333444
# Enable Logging
LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=True

# fix octavia
enable_service octavia
GLANCE_LIMIT_IMAGE_SIZE_TOTAL=10000
# LIBVIRT_TYPE=kvm

# Add magnum patch e.g.
#MAGNUM_REPO=https://review.opendev.org/openstack/magnum
#MAGNUM_BRANCH=refs/changes/76/851076/10

[[post-config|/etc/neutron/neutron.conf]]
[DEFAULT]
advertise_mtu = True
global_physnet_mtu = 1400
EOF

# Fix permissions on current tty so screens can attach
sudo chmod go+rw `tty`

# Stack that stack!
/opt/stack/stack.sh

#
# Setup k8s for Cluster API
#

# Install `kubectl` CLI
curl -Lo /tmp/kubectl "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl

# Install Docker
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
sudo usermod -aG docker $USER

# Docker tinks with firewalls
sudo iptables -I DOCKER-USER -j ACCEPT

# Install `kind` CLI
sudo curl -Lo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/v0.16.0/kind-linux-amd64
sudo chmod +x /usr/local/bin/kind

# Create a `kind` cluster inside "docker" group
newgrp docker <<EOF
kind create cluster
EOF

# install helm
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh

# install cert manager
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm upgrade cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --version v1.10.1 --set installCRDs=true --wait

# install cluster api resources
CAPI_VERSION="v1.2.5"
CAPO_VERSION="v0.7.0-stackhpc.1"
mkdir -p capi
cat <<EOF > capi/kustomization.yaml
---
patches:
-   patch: "- op: replace\n  path: /spec/template/spec/containers/0/args\n  value:\n
        \   - --leader-elect\n    - --metrics-bind-addr=localhost:8080"
    target:
        kind: Deployment
        name: capi-controller-manager
        namespace: capi-system
-   patch: "- op: replace\n  path: /spec/template/spec/containers/0/args\n  value:\n
        \   - --leader-elect\n    - --metrics-bind-addr=localhost:8080"
    target:
        kind: Deployment
        name: capi-kubeadm-bootstrap-controller-manager
        namespace: capi-kubeadm-bootstrap-system
-   patch: "- op: replace\n  path: /spec/template/spec/containers/0/args\n  value:\n
        \   - --leader-elect\n    - --metrics-bind-addr=localhost:8080"
    target:
        kind: Deployment
        name: capi-kubeadm-control-plane-controller-manager
        namespace: capi-kubeadm-control-plane-system
resources:
- https://github.com/kubernetes-sigs/cluster-api/releases/download/$CAPI_VERSION/cluster-api-components.yaml
- https://github.com/stackhpc/cluster-api-provider-openstack/releases/download/$CAPO_VERSION/infrastructure-components.yaml
EOF
kubectl apply -k capi

# install add on manager
ADDON_VERSION="0.1.0-dev.0.main.21"
helm install --repo https://stackhpc.github.io/cluster-api-addon-provider cluster-api-addon-provider --version $ADDON_VERSION -n capi-addon-system --create-namespace --wait --timeout 30m cluster-api-addon-provider

echo helm install 13d2f0eb-61fa-4340-a41b-6d7bdae9cfb6 https://stackhpc.github.io/capi-helm-charts/openstack-cluster-0.1.1-dev.0.main.2.tgz  --output json --timeout 5m <<EOF
kubernetesVersion: "v1.25.5"
machineImage: "kube-1.25.5"
cloudCredentialsSecretName: "project-63e5f76dc3ab44f8b2c1f89aa94d76ea"
apiServer:
  enableLoadBalancer: false
controlPlane:
  machineFlavor: "ds2G"
  machineCount: 1
nodeGroups:
  - name: default
    machineFlavor: "ds2G"
    machineCount: 1
    autoscale: false
    # machineCountMin
    # machineCountMax
EOF

source /opt/stack/openrc admin admin

pip install python-magnumclient

# add k8s image
KUBE_IMAGE_URL=https://minio.services.osism.tech/openstack-k8s-capi-images/ubuntu-2004-kube-v1.25/ubuntu-2004-kube-v1.25.5.qcow2
curl $KUBE_IMAGE_URL > kube.qcow2
openstack image create kube-1.25.5 --file kube.qcow2 --disk-format qcow2 --community
openstack image set kube-1.25.5 --os-distro ubuntu --os-version 20.04
rm kube.qcow2

# register template and test it
openstack coe cluster template create kube-1.25.5 --image kube-1.25.5 --coe kubernetes --external-network public --labels kube_tag=v1.25.5 --flavor ds2G --master-flavor ds2G
openstack coe cluster create devstacktest --cluster-template kube-1.25.5
openstack coe cluster list

# get creds
openstack coe cluster config devstacktest
# TODO: run sonoboy
