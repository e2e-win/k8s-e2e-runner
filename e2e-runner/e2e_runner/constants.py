AZURE_LOCATIONS = [
    "northcentralus"
]

COMPUTE_QUOTAS = [
    "virtualMachines",
    "cores",
    "standardDSv3Family",
    "PremiumDiskCount",
]
NETWORK_QUOTAS = [
    "VirtualNetworks",
    "NetworkInterfaces"
    "NetworkSecurityGroups",
    "LoadBalancers",
    "PublicIPAddresses",
    "RouteTables",
]

DEFAULT_KUBERNETES_VERSION = "v1.32.0"
DEFAULT_AKS_VERSION = "1.30.6"

FLANNEL_NAMESPACE = "kube-flannel"
FLANNEL_HELM_REPO = "https://flannel-io.github.io/flannel"
FLANNEL_HELM_VERSION = "v0.26.3"
FLANNEL_MODE_OVERLAY = "vxlan"
FLANNEL_MODE_L2BRIDGE = "host-gw"

CLOUD_PROVIDER_AZURE_HELM_REPO = "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/{}/helm/repo"  # noqa: E501
CLOUD_PROVIDER_AZURE_TAGS = "https://api.github.com/repos/kubernetes-sigs/cloud-provider-azure/tags"  # noqa: E501
