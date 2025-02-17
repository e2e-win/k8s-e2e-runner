FROM ubuntu:22.04

# Corresponds to the Prow K8s environment
ENV KUBECTL_VERSION=v1.29.11

# Install APT packages
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
	apt-get install -y curl dateutils jq openssh-client

# APT cleanup
RUN apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/apt/lists/*

# Install kubectl
RUN curl -Lo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    chmod +x /usr/local/bin/kubectl

# Install Azure CLI
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

ADD backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]
