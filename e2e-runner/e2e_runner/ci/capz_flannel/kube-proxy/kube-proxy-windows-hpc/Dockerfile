ARG K8S_VERSION="v1.31.6"
ARG CNI_PLUGINS_VERSION="v1.3.0"

# Linux stage
FROM --platform=linux/amd64 alpine:latest as prep

ARG K8S_VERSION
ARG CNI_PLUGINS_VERSION

RUN mkdir -p /kube-proxy /cni/bin
ADD https://dl.k8s.io/${K8S_VERSION}/bin/windows/amd64/kube-proxy.exe /kube-proxy/kube-proxy.exe
ADD https://github.com/containernetworking/plugins/releases/download/${CNI_PLUGINS_VERSION}/cni-plugins-windows-amd64-${CNI_PLUGINS_VERSION}.tgz /cni-plugins.tgz
RUN tar -xzvf /cni-plugins.tgz -C /cni/bin

# Golang build stage
FROM --platform=linux/amd64 golang:latest as gobuilder

ADD . /kube-proxy-windows
WORKDIR /kube-proxy-windows
ENV GOOS=windows GOARCH=amd64
RUN go build -ldflags="-s -w" -o init.exe ./cmd/init

# Windows stage
FROM mcr.microsoft.com/oss/kubernetes/windows-host-process-containers-base-image:v1.0.0

COPY --from=prep /kube-proxy /kube-proxy
COPY --from=prep /cni /cni
COPY --from=gobuilder /kube-proxy-windows/init.exe /kube-proxy/init.exe

ENV KUBE_PROXY_CONF="/var/lib/kube-proxy/config.conf"
ENV KUBE_PROXY_WINDOWS_CONF="/k/kube-proxy/config.conf"

ENV PATH="C:\Windows\system32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"

ENTRYPOINT ["%CONTAINER_SANDBOX_MOUNT_POINT%\\kube-proxy\\kube-proxy.exe"]
