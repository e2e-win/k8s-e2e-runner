AZURE_LOCATIONS = ["eastus2", "westeurope", "westus2", "southcentralus"]
WINDOWS_ADMIN_USER = "azureuser"

tunnel_ports = {
	"test-min0": "5990",
	"test-min1": "5991",
	"test-min2": "5992"
}

KUBERNETES_LINUX_BINS_LOCATION = "_output/local/bin/linux/amd64/"
KUBERNETES_WINDOWS_BINS_LOCATION = "_output/local/bin/windows/amd64"

SDN_BINS_LOCATION = "out"
CONTAINERD_BINS_LOCATION = "_output"
CONTAINERD_SHIM_DIR = "./cmd/containerd-shim-runhcs-v1"
CONTAINERD_SHIM_BIN = "containerd-shim-runhcs-v1.exe"
CONTAINERD_CTR_LOCATION = "bin/ctr.exe"
