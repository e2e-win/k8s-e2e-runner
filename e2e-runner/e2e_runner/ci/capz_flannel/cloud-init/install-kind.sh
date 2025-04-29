#!/usr/bin/env bash
set -o nounset
set -o pipefail
set -o errexit

KIND_BIN_URL="https://github.com/kubernetes-sigs/kind/releases/download/v0.27.0/kind-linux-amd64"

sudo curl -s -L -o /usr/local/bin/kind $KIND_BIN_URL
sudo chmod +x /usr/local/bin/kind

PUBLIC_IP=$(curl -s -H Metadata:true 'http://169.254.169.254/metadata/instance?api-version=2017-04-02' | \
            jq -r '.network.interface[0].ipv4.ipAddress[0].publicIpAddress')

cat <<EOF > $HOME/kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: "0.0.0.0"
  apiServerPort: 6443
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: ClusterConfiguration
    apiServer:
      certSANs:
      - ${PUBLIC_IP}
EOF
