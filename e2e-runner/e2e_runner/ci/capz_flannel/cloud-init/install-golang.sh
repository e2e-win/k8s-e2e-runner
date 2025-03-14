#!/usr/bin/env bash
set -o nounset
set -o pipefail
set -o errexit

GO_LATEST_VERSION=$(curl -s -L https://golang.org/VERSION\?m\=text | head -1)

curl -s -O https://dl.google.com/go/${GO_LATEST_VERSION}.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf ${GO_LATEST_VERSION}.linux-amd64.tar.gz
rm ${GO_LATEST_VERSION}.linux-amd64.tar.gz

sudo ln -s /usr/local/go/bin/go /usr/local/bin/go
go version
