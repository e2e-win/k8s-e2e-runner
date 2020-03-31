FROM ubuntu:latest

ENV K8S_VERSION=1.17.3
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
	apt-get install -y \
		curl \
		dateutils \
		openssh-client && \
	rm -rf /var/lib/apt/lists/*

# Install kubernetes client
RUN curl -fsSL https://dl.k8s.io/v${K8S_VERSION}/kubernetes-client-linux-amd64.tar.gz -o /tmp/k8s.tar.gz && \
	tar -zxf /tmp/k8s.tar.gz -C /tmp/ && \
	mv /tmp/kubernetes/client/bin/kubectl /usr/local/bin/kubectl && \
	rm -rf /tmp/k8s.tar.gz /tmp/kubernetes

# Install Azure CLI
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

ADD backup.sh /usr/local/bin/backup.sh

RUN chmod +x /usr/local/bin/backup.sh

CMD /usr/local/bin/backup.sh
