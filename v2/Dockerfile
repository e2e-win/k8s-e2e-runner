FROM ubuntu:bionic

ENV GO_VERSION=1.13.7

RUN apt-get update && \
	apt-get install -y \
		build-essential \
		curl \
		git \
		libffi-dev \
		libssl-dev \
		software-properties-common \
		python \
		python-dev \
		python-openssl \
		python-pip \
		python3-pip \
		rsync \
		unzip \
		wget

RUN pip install --upgrade \
	crudini \
	pip \
	python-novaclient \
	python-openstackclient \
	setuptools

# Install Ansible
RUN apt-add-repository ppa:ansible/ansible -y && \
	apt-get update && \
	apt-get install ansible -y
RUN pip install pywinrm

# Install Golang

RUN curl -O https://dl.google.com/go/go${GO_VERSION}.linux-amd64.tar.gz
RUN tar -xf go${GO_VERSION}.linux-amd64.tar.gz
RUN mv go /usr/local
RUN rm -rf go
RUN rm go${GO_VERSION}.linux-amd64.tar.gz
ENV GOPATH "/go"
ENV PATH "$PATH:/usr/local/go/bin:$GOPATH/bin"
RUN mkdir -p /go/src

RUN pip install configargparse

# Install gcloud cli

ENV PATH=/google-cloud-sdk/bin:/workspace:${PATH} \
    CLOUDSDK_CORE_DISABLE_PROMPTS=1

RUN wget -q https://dl.google.com/dl/cloudsdk/channels/rapid/google-cloud-sdk.tar.gz && \
    tar xzf google-cloud-sdk.tar.gz -C / && \
    rm google-cloud-sdk.tar.gz && \
    /google-cloud-sdk/install.sh \
        --disable-installation-options \
        --bash-completion=false \
        --path-update=false \
        --usage-reporting=false && \
    gcloud components install alpha beta && \
    gcloud info | tee /gcloud-info.txt

RUN wget -q https://releases.hashicorp.com/terraform/0.11.13/terraform_0.11.13_linux_amd64.zip && \
    unzip terraform_0.11.13_linux_amd64.zip && \
    rm terraform_0.11.13_linux_amd64.zip && \
    mv terraform /usr/local/bin

RUN pip install azure

WORKDIR /workspace
ADD bootstrap.py  /workspace

RUN chmod 755 bootstrap.py
ENTRYPOINT ["./bootstrap.py"]

