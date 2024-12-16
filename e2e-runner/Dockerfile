FROM ubuntu:24.04

ARG CAPI_VERSION=v1.8.4
ARG KUBECTL_VERSION=v1.30.6

# Install system APT packages & dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y \
        curl git python3 python3-pip openssh-client rsync net-tools vim tzdata

# APT cleanup
RUN apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
ADD requirements.txt /tmp
RUN pip3 install -r /tmp/requirements.txt --break-system-packages && \
    rm /tmp/requirements.txt

# Install clusterctl
RUN curl -Lo /usr/local/bin/clusterctl "https://github.com/kubernetes-sigs/cluster-api/releases/download/${CAPI_VERSION}/clusterctl-linux-amd64" && \
    chmod +x /usr/local/bin/clusterctl

# Install kubectl
RUN curl -Lo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    chmod +x /usr/local/bin/kubectl && \
    mkdir ~/.kube
ENV KUBECTL_PATH="/usr/local/bin/kubectl"

# Install helm
RUN curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 && \
    chmod +x get_helm.sh && \
    ./get_helm.sh && \
    rm ./get_helm.sh

WORKDIR /workspace

ADD cleanup-azure-rgs.py /workspace
RUN chmod +x cleanup-azure-rgs.py

ADD entrypoint.sh /workspace
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/workspace/entrypoint.sh"]
