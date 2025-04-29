ARG FLANNEL_VERSION="v0.26.6"
ARG CNI_PLUGINS_VERSION="v1.7.1"
ARG WINDOWS_CNI_PLUGINS_VERSION="v0.3.1"
ARG FLANNEL_CNI_PLUGIN_VERSION="v1.6.2-flannel1"

# Linux stage
FROM --platform=linux/amd64 alpine:latest as prep

ARG FLANNEL_VERSION
ARG CNI_PLUGINS_VERSION
ARG WINDOWS_CNI_PLUGINS_VERSION
ARG FLANNEL_CNI_PLUGIN_VERSION

RUN mkdir -p /flannel /cni/bin
ADD https://github.com/flannel-io/flannel/releases/download/${FLANNEL_VERSION}/flanneld.exe /flannel/flanneld.exe
ADD https://github.com/containernetworking/plugins/releases/download/${CNI_PLUGINS_VERSION}/cni-plugins-windows-amd64-${CNI_PLUGINS_VERSION}.tgz /cni-plugins.tgz
ADD https://github.com/microsoft/windows-container-networking/releases/download/${WINDOWS_CNI_PLUGINS_VERSION}/windows-container-networking-cni-amd64-${WINDOWS_CNI_PLUGINS_VERSION}.zip /windows-cni-plugins.zip
ADD https://github.com/flannel-io/cni-plugin/releases/download/${FLANNEL_CNI_PLUGIN_VERSION}/flannel-amd64.exe /cni/bin/flannel.exe
RUN tar -xzvf /cni-plugins.tgz -C /cni/bin
RUN unzip /windows-cni-plugins.zip -d /cni/bin

# Golang build stage
FROM --platform=linux/amd64 golang:latest as gobuilder

ADD . /flannel-windows
WORKDIR /flannel-windows
ENV GOOS=windows GOARCH=amd64
RUN go build -ldflags="-s -w" -o install-cni.exe ./cmd/install-cni

# Windows stage
FROM mcr.microsoft.com/oss/kubernetes/windows-host-process-containers-base-image:v1.0.0

COPY --from=prep /flannel /flannel
COPY --from=prep /cni /cni
COPY --from=gobuilder /flannel-windows/install-cni.exe /flannel/install-cni.exe

ENV CONTAINERD_CNI_BIN_DIR="/opt/cni/bin"
ENV FLANNEL_NET_CONF="/etc/kube-flannel/net-conf.json"
ENV FLANNEL_CNI_CONF="/etc/cni/net.d/10-flannel.conf"

ENV PATH="C:\Windows\system32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"

ENTRYPOINT ["%CONTAINER_SANDBOX_MOUNT_POINT%\\flannel\\flanneld.exe"]
