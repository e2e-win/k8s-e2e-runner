import base64
import json
import os
import shutil
import stat
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse

import tenacity
import yaml
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient
from e2e_runner import base as e2e_base
from e2e_runner import constants as e2e_constants
from e2e_runner import exceptions as e2e_exceptions
from e2e_runner import logger as e2e_logger
from e2e_runner.ci.capz_flannel import bootstrap_vm
from e2e_runner.utils import azure as e2e_azure_utils
from e2e_runner.utils import kubernetes as e2e_k8s_utils
from e2e_runner.utils import utils as e2e_utils


class CapzFlannelCI(e2e_base.CI):

    def __init__(self, opts):
        super(CapzFlannelCI, self).__init__(opts)

        self.capz_flannel_dir = os.path.dirname(__file__)
        self.logging = e2e_logger.get_logger(__name__)

        self.resource_group_tags = e2e_azure_utils.get_resource_group_tags()
        self.kubernetes_version = self.opts.kubernetes_version
        self.bins_built = []
        self.mgmt_kubeconfig_path = os.path.join(
            self.kubeconfig_dir, "mgmt-kubeconfig.yaml")

        self.ssh_private_key_path = os.environ["SSH_PRIVATE_KEY_PATH"]
        self.ssh_public_key = e2e_utils.get_file_content(
            os.environ["SSH_PUBLIC_KEY_PATH"])

        creds, sub_id = e2e_azure_utils.get_credentials()
        self.mgmt_client = ResourceManagementClient(creds, sub_id)
        self.network_client = NetworkManagementClient(creds, sub_id)
        self.compute_client = ComputeManagementClient(creds, sub_id)
        self.location = self._get_location(opts.location)

        self.bootstrap_vm = bootstrap_vm.BootstrapVM(opts, self.location)

    @property
    def mgmt_k8s_client(self):
        return e2e_k8s_utils.KubernetesClient(
            config_file=self.mgmt_kubeconfig_path)

    @property
    def control_plane_public_address(self):
        if os.path.exists(self.kubeconfig_path):
            control_plane_address, _ = self._parse_capz_kubeconfig()
            return control_plane_address

        if self.bootstrap_vm.is_deployed:
            return self._get_capz_control_plane_address()

        raise e2e_exceptions.KubernetesEndpointNotFound(
            "Could not find Kubernetes control-plane address")

    @property
    def control_plane_public_port(self):
        if os.path.exists(self.kubeconfig_path):
            _, control_plane_port = self._parse_capz_kubeconfig()
            return control_plane_port

        if self.bootstrap_vm.is_deployed:
            return self._get_capz_control_plane_port()

        raise e2e_exceptions.KubernetesEndpointNotFound(
            "Could not find Kubernetes control-plane port")

    @property
    def linux_private_addresses(self):
        return self._get_agents_private_addresses("linux")

    @property
    def windows_private_addresses(self):
        return self._get_agents_private_addresses("windows")

    @property
    def capz_images_publisher(self):
        return "cncf-upstream"

    @property
    def capz_images_linux_offer(self):
        return "capi"

    @property
    def capz_images_windows_offer(self):
        return "capi-windows"

    @property
    def capz_images_ubuntu_sku(self):
        return "ubuntu-2204-gen1"

    @property
    def os_version(self):
        if self.opts.win_os == "ltsc2019":
            os_version = 2019
        elif self.opts.win_os == "ltsc2022":
            os_version = 2022
        elif self.opts.win_os == "ltsc2025":
            os_version = 2025
        else:
            raise e2e_exceptions.InvalidOperatingSystem(
                f"Unknown win_os: {self.opts.win_os}"
            )
        return os_version

    @property
    def capz_images_windows_name(self):
        return f"windows-{self.os_version}-containerd-gen1"

    @property
    def capz_sig_windows_image_name(self):
        return f"capi-win-{self.os_version}-containerd"
    
    @property
    def capz_sig_ubuntu_image_name(self):
        return f"capi-ubun2-2404"

    @property
    def capz_sig_image_gallery(self):
        return f"ClusterAPI-f72ceb4f-5159-4c26-a0fe-2ea738f0d019"

    @property
    def k8s_path(self):
        return os.path.join(
            self.bootstrap_vm.go_path,
            "src", "k8s.io", "kubernetes")

    @property
    def containerd_path(self):
        return os.path.join(
            self.bootstrap_vm.go_path,
            "src", "github.com", "containerd", "containerd")

    @property
    def containerd_shim_path(self):
        return os.path.join(
            self.bootstrap_vm.go_path,
            "src", "github.com", "Microsoft", "hcsshim")

    @property
    def cri_tools_path(self):
        return os.path.join(
            self.bootstrap_vm.go_path,
            "src", "github.com", "kubernetes-sigs", "cri-tools")

    @property
    def sdn_path(self):
        return os.path.join(
            self.bootstrap_vm.go_path,
            "src", "github.com", "Microsoft", "windows-container-networking")

    def setup_bootstrap_vm(self):
        self.bootstrap_vm.setup()
        self.bootstrap_vm.upload(
            local_path=os.path.join(self.e2e_runner_dir, "scripts"),
            remote_path="www/",
        )

    def cleanup_bootstrap_vm(self):
        self.bootstrap_vm.remove()

    def build(self, bins_to_build):
        builder_mapping = {
            "k8sbins": self._build_k8s_artifacts,
            "containerdbins": self._build_containerd_binaries,
            "containerdshim": self._build_containerd_shim,
            "critools": self._build_cri_tools,
            "sdncnibins": self._build_sdn_cni_binaries,
        }
        for bins in bins_to_build:
            self.logging.info("Building %s", bins)
            build_func = builder_mapping.get(bins)
            if not build_func:
                raise e2e_exceptions.BuildFailed(f"Cannot build {bins}")
            build_func()
            self.bins_built.append(bins)

    def up(self):
        self._create_metadata_artifact()
        self._setup_capz_cluster()

    def down(self):
        self.bootstrap_vm.remove()
        self._delete_capz_rg()

    def collect_logs(self):
        if self.bootstrap_vm.is_deployed:
            self._collect_bootstrap_vm_logs()
        self._collect_linux_logs()
        self._collect_windows_logs()

    def _get_location(self, location=None):
        if not location:
            location = e2e_azure_utils.get_least_used_location(
                self.compute_client, self.network_client)
        self.logging.info("Using Azure location %s", location)
        return location

    def _delete_capz_rg(self, wait=False):
        self.logging.info("Deleting CAPZ cluster resource group")
        e2e_azure_utils.delete_resource_group(
            self.mgmt_client, self.opts.cluster_name, wait=wait)

    def _collect_bootstrap_vm_logs(self):
        self.logging.info("Collecting logs from bootstrap VM")
        try:
            self._get_mgmt_cluster_pods_logs()

            suffix = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            e2e_utils.make_tgz_archive(
                self.bootstrap_vm.logs_dir,
                f"{self.bootstrap_vm.logs_dir}_{suffix}.tgz")

            shutil.rmtree(self.bootstrap_vm.logs_dir)

        except Exception as e:
            self.logging.warning("Cannot collect logs from bootstrap VM. "
                                 "Exception details: \n%s", e)
            self.logging.warning("Skipping bootstrap VM logs collection")

    def _get_mgmt_cluster_pods_logs(self):
        os.makedirs(self.bootstrap_vm.logs_dir, exist_ok=True)
        pods_yaml, _ = e2e_utils.exec_kubectl(  # pyright: ignore
            args=[
                "get", "pods", "--kubeconfig", self.mgmt_kubeconfig_path,
                "-A", "-o", "yaml"
            ],
            capture_output=True,
            hide_cmd=True,
        )
        pods = yaml.safe_load(pods_yaml)  # pyright: ignore
        for pod in pods['items']:
            pod_name = pod['metadata']['name']
            ns_name = pod['metadata']['namespace']
            for container in pod['spec']['containers']:
                container_name = container['name']
                pod_logs, _ = e2e_utils.exec_kubectl(  # pyright: ignore
                    args=[
                        "logs", "--kubeconfig", self.mgmt_kubeconfig_path,
                        "-n", ns_name, pod_name, container_name,
                    ],
                    capture_output=True,
                    hide_cmd=True,
                )
                log_file = os.path.join(
                    self.bootstrap_vm.logs_dir,
                    f"{ns_name}_{pod_name}_{container_name}.log",
                )
                with open(log_file, 'wb') as f:
                    f.write(pod_logs.encode())  # pyright: ignore

    def _collect_windows_logs(self):
        if not self._can_collect_logs():
            self.logging.info("Skipping logs collection.")
            return

        local_script_path = os.path.join(
            self.e2e_runner_dir,
            "scripts/collect-logs.ps1"
        )
        remote_script_path = os.path.join(
            "/tmp",
            os.path.basename(local_script_path)
        )
        for node_address in self.windows_private_addresses:
            try:
                self._collect_logs(
                    node_address,
                    local_script_path=local_script_path,
                    remote_script_path=remote_script_path,
                    remote_cmd=remote_script_path,
                    remote_logs_archive="/tmp/logs.tgz"
                )
            except Exception as ex:
                self.logging.warning(
                    "Cannot collect logs from node %s. Exception details: "
                    "%s. Skipping", node_address, ex)

    def _collect_linux_logs(self):
        if not self._can_collect_logs():
            self.logging.info("Skipping logs collection.")
            return

        local_script_path = os.path.join(
            self.e2e_runner_dir,
            "scripts/collect-logs.sh"
        )
        remote_script_path = os.path.join(
            "/tmp",
            os.path.basename(local_script_path)
        )
        for node_address in self.linux_private_addresses:
            try:
                self._collect_logs(
                    node_address=node_address,
                    local_script_path=local_script_path,
                    remote_script_path=remote_script_path,
                    remote_cmd=f"sudo bash {remote_script_path}",
                    remote_logs_archive="/tmp/logs.tgz"
                )
            except Exception as ex:
                self.logging.warning(
                    "Cannot collect logs from node %s. Exception details: "
                    "%s. Skipping", node_address, ex)

    def _can_collect_logs(self):
        if "KUBECONFIG" not in os.environ:
            self.logging.warning(
                "Cannot collect logs, because KUBECONFIG is not set.")
            return False
        ssh_config = os.path.join(os.environ["HOME"], ".ssh/config")
        if not os.path.exists(ssh_config):
            self.logging.warning(
                "Cannot collect logs, because the ssh config file is not set.")
            return False
        return True

    def _collect_logs(self, node_address, local_script_path,
                      remote_script_path, remote_cmd, remote_logs_archive):
        self.logging.info("Collecting logs from node %s", node_address)

        if not self._has_node_ssh_connection(node_address):
            self.logging.warning(
                "No SSH connectivity to node %s. Skipping logs collection",
                node_address)
            return

        self._upload_to_node(
            node_address,
            local_script_path,
            remote_script_path
        )
        self._run_node_cmd(
            node_address,
            remote_cmd
        )
        node_name, _ = self._run_node_cmd(node_address, "hostname")
        local_logs_archive = os.path.join(
            self.opts.artifacts_directory,
            f"{node_name}-{os.path.basename(remote_logs_archive)}",
        )
        self._download_from_node(
            node_address,
            remote_logs_archive,
            local_logs_archive,
        )

        self.logging.info("Finished collecting logs from node %s", node_name)

    def _get_agents_private_addresses(self, operating_system):
        private_addresses, _ = e2e_utils.exec_kubectl(  # pyright: ignore
            args=[
                "get", "nodes", "--kubeconfig", self.kubeconfig_path,
                "-o", "jsonpath=\"{{.items[?(@.status.nodeInfo.operatingSystem == '{}')].status.addresses[?(@.type == 'InternalIP')].address}}\"".format(operating_system),  # noqa:
            ],
            capture_output=True,
            hide_cmd=True,
        )
        return private_addresses.strip().split()  # pyright: ignore

    def _has_node_ssh_connection(self, node_address):
        try:
            e2e_utils.run_shell_cmd(
                cmd=[
                    "ssh", self.control_plane_public_address,
                    f"'nc -w 5 -z {node_address} 22'"
                ],
                hide_cmd=True,
                timeout=60,
            )
        except e2e_exceptions.ShellCmdFailed:
            return False
        return True

    @e2e_utils.retry_on_error()
    def _run_node_cmd(self, node_address, cmd):
        stdout, stderr = e2e_utils.run_shell_cmd(
            cmd=["ssh", node_address, f"'{cmd}'"],
            timeout=600,
            capture_output=True,
        )
        if stdout:
            stdout = stdout.decode().strip()
        if stderr:
            stderr = stderr.decode().strip()
        return stdout, stderr

    @e2e_utils.retry_on_error()
    def _download_from_node(self, node_address, remote_path, local_path):
        e2e_utils.run_shell_cmd(
            cmd=["scp", "-r", f"{node_address}:{remote_path}", local_path],
            timeout=600,
        )

    @e2e_utils.retry_on_error()
    def _upload_to_node(self, node_address, local_path, remote_path):
        e2e_utils.run_shell_cmd(
            cmd=["scp", "-r", local_path, f"{node_address}:{remote_path}"],
            timeout=600,
        )

    def _parse_capz_kubeconfig(self):
        with open(self.kubeconfig_path, 'r') as f:
            cfg = yaml.safe_load(f.read())

        endpoint = urlparse(cfg["clusters"][0]["cluster"]["server"])
        k8s_address = endpoint.hostname
        k8s_port = endpoint.port

        if k8s_port is None:
            if endpoint.scheme == "http":
                k8s_port = 80
            elif endpoint.scheme == "https":
                k8s_port = 443
            else:
                raise e2e_exceptions.InvalidKubernetesEndpoint(
                    f"Found endpoint scheme: {endpoint.scheme}, but only "
                    "http or https are valid")

        return k8s_address, k8s_port

    def _get_capz_control_plane_address(self):
        control_plane_address, _ = e2e_utils.exec_kubectl(  # pyright: ignore # noqa:
            args=[
                "get", "cluster", self.opts.cluster_name,
                "--kubeconfig", self.mgmt_kubeconfig_path,
                "--no-headers",
                "-o", "custom-columns=ADDRESS:.spec.controlPlaneEndpoint.host",  # noqa:
            ],
            capture_output=True,
        )
        return control_plane_address.strip()  # pyright: ignore

    def _get_capz_control_plane_port(self):
        control_plane_port, _ = e2e_utils.exec_kubectl(  # pyright: ignore # noqa:
            args=[
                "get", "cluster", self.opts.cluster_name,
                "--kubeconfig", self.mgmt_kubeconfig_path,
                "--no-headers",
                "-o", "custom-columns=PORT:.spec.controlPlaneEndpoint.port",  # noqa:
            ],
            capture_output=True,
        )
        return control_plane_port.strip()  # pyright: ignore

    @e2e_utils.retry_on_error(max_attempts=6, max_sleep_seconds=0)
    def _setup_capz_cluster(self):
        try:
            start = time.time()
            self._setup_mgmt_cluster()
            self._setup_mgmt_kubeconfig()
            self._setup_capz_components()
            self._create_capz_cluster()
            self._wait_capz_control_plane(timeout=600)
            self._setup_capz_kubeconfig()
            self._add_azure_cloud_provider()
            self._add_flannel_cni()
            self._wait_windows_agents(timeout=600)
            self._setup_ssh_config()
            self._add_kube_proxy_windows()
            self.k8s_client.wait_running_pods()
            self._validate_k8s_api_versions()
            elapsed = time.time() - start
            self.logging.info(
                "The cluster provisioned in %.2f minutes", elapsed / 60)
        except Exception as ex:
            self.logging.error(
                "Failed to create CAPZ cluster. Exception details: %s", ex)
            self._cleanup_capz_cluster()
            raise ex

    def _setup_mgmt_cluster(self):
        self.logging.info("Setting up the management cluster")
        self.bootstrap_vm.exec(
            script=[
                "kind create cluster --config ~/kind-config.yaml --wait 15m"
            ],
        )

    def _setup_mgmt_kubeconfig(self):
        self.logging.info("Setting up the management cluster kubeconfig")
        self.bootstrap_vm.download(
            ".kube/config",
            self.mgmt_kubeconfig_path
        )
        with open(self.mgmt_kubeconfig_path, 'r') as f:
            cfg = yaml.safe_load(f.read())

        public_endpoint = f"https://{self.bootstrap_vm.public_ip}:6443"
        cfg["clusters"][0]["cluster"]["server"] = public_endpoint

        with open(self.mgmt_kubeconfig_path, 'w') as f:
            f.write(yaml.safe_dump(cfg))

    def _setup_capz_components(self):
        self.logging.info("Creating CAPI cluster identity")
        self.mgmt_k8s_client.create_secret(
            name="cluster-identity-secret",
            secret_name="clientSecret",
            secret_value=os.environ["AZURE_CLIENT_SECRET"],
        )
        self.logging.info("Setup the Azure Cluster API components")
        clusterctlEnv = {}
        if os.environ.get("GITHUB_TOKEN"):
            clusterctlEnv["GITHUB_TOKEN"] = os.environ.get("GITHUB_TOKEN")
        e2e_utils.retry_on_error()(e2e_utils.run_shell_cmd)(
            cmd=[
                "clusterctl", "init",
                "--kubeconfig", self.mgmt_kubeconfig_path,
                "--infrastructure", "azure",
                "--wait-providers",
            ],
            env=clusterctlEnv,
        )

    def _create_capz_cluster(self):
        self.logging.info("Create CAPZ cluster")
        output_file = "/tmp/capz-cluster.yaml"
        e2e_utils.render_template(
            template_file="cluster.yaml.j2",
            output_file=output_file,
            context=self._get_capz_context(),
            searchpath=f"{self.e2e_runner_dir}/templates/capz",
        )
        e2e_utils.exec_kubectl([
            "apply", "--kubeconfig", self.mgmt_kubeconfig_path,
            "-f", output_file
        ])

    def _get_capz_context(self):
        control_plane_subnet_cidr = self.opts.control_plane_subnet_cidr_block
        ssh_public_key_b64 = base64.b64encode(
            self.ssh_public_key.encode()).decode()  # pyright: ignore
        capz_image_ubuntu_version = self._capz_image_latest_version(
            self.capz_sig_image_gallery, self.capz_sig_ubuntu_image_name
        )
        capz_image_windows_version = self._capz_image_latest_version(
            self.capz_sig_image_gallery, self.capz_sig_windows_image_name
        )
        context = {
            "cluster_name": self.opts.cluster_name,
            "resource_group_tags": self.resource_group_tags,

            "bootstrap_vm_vnet_name": self.bootstrap_vm.vnet_name,
            "bootstrap_vm_endpoint": f"{self.bootstrap_vm.private_ip}:8081",

            "vnet_cidr": self.opts.vnet_cidr_block,
            "control_plane_subnet_cidr": control_plane_subnet_cidr,
            "node_subnet_cidr": self.opts.node_subnet_cidr_block,
            "cluster_network_subnet": self.opts.cluster_network_subnet,
            "cni_version": self.opts.cni_version,

            "azure_location": self.location,
            "azure_subscription_id": os.environ["AZURE_SUBSCRIPTION_ID"],
            "azure_tenant_id": os.environ["AZURE_TENANT_ID"],
            "azure_client_id": os.environ["AZURE_CLIENT_ID"],
            "azure_client_secret": os.environ["AZURE_CLIENT_SECRET"],
            "azure_ssh_public_key": self.ssh_public_key,
            "azure_ssh_public_key_b64": ssh_public_key_b64,

            "master_vm_size": self.opts.master_vm_size,
            "win_agents_count": self.opts.win_agents_count,
            "win_agent_size": self.opts.win_agent_size,

            "kubernetes_version": self.kubernetes_version,
            "flannel_mode": self.opts.flannel_mode,
            "k8s_bins": "k8sbins" in self.bins_built,
            "sdn_cni_bins": "sdncnibins" in self.bins_built,
            "containerd_bins": "containerdbins" in self.bins_built,
            "containerd_shim_bins": "containerdshim" in self.bins_built,

            "capz_sig_ubuntu_image_name": self.capz_sig_ubuntu_image_name,
            "capz_sig_ubuntu_image_version": capz_image_ubuntu_version,

            "capz_sig_windows_image_name": self.capz_sig_windows_image_name,
            "capz_sig_windows_image_version": capz_image_windows_version
        }
        return context

    def _capz_images_version_prefix(self):
        ver = self.kubernetes_version
        if "k8sbins" in self.bins_built:
            ver = e2e_constants.DEFAULT_KUBERNETES_VERSION
        v = ver.strip("v").split(".")
        return f"{v[0]}{v[1]}.{v[2]}"
    
    def _capz_sig_gallery_version_prefix(self, is_node_setup = False):
        ver = self.kubernetes_version
        if "k8sbins" in self.bins_built and is_node_setup:
            ver = e2e_constants.DEFAULT_KUBERNETES_VERSION
        v = ver.strip("v").split(".")
        return f"{v[0]}.{v[1]}"

    def _capz_image_latest_version(self, gallery_name, image_name):
        img_vers = e2e_utils.retry_on_error()(
            self.compute_client.community_gallery_image_versions.list)(
                location=self.location,
                public_gallery_name=gallery_name,
                gallery_image_name=image_name
            )
        prefix = self._capz_sig_gallery_version_prefix(is_node_setup=True)
        vers = [i.name for i in img_vers if i.name.startswith(prefix)]
        vers.sort()
        return vers[-1]

    def _wait_capz_control_plane(self, timeout=1800):
        self.logging.info(
            "Waiting up to %.2f minutes for the CAPZ control-plane",
            timeout / 60.0)
        self._wait_capz_machines(
            wanted_count=1,
            selector="cluster.x-k8s.io/control-plane=",
            status="Provisioned",
            timeout=timeout)
        self.logging.info("Control-plane is ready")

    def _wait_windows_agents(self, timeout=5400):
        self.logging.info(
            "Waiting up to %.2f minutes for the Windows agents",
            timeout / 60.0)
        self._wait_capz_machines(
            wanted_count=2,
            selector="cluster.x-k8s.io/deployment-name={}-md-win".format(
                self.opts.cluster_name),
            timeout=timeout,
        )
        self.logging.info("Windows agents are ready")

    def _wait_capz_machines(self, wanted_count, selector="", status="Running",
                            timeout=3600):
        for attempt in tenacity.Retrying(
                stop=tenacity.stop_after_delay(timeout),  # pyright: ignore
                wait=tenacity.wait_exponential(max=30),  # pyright: ignore
                retry=tenacity.retry_if_exception_type(AssertionError),  # pyright: ignore # noqa:
                reraise=True):
            with attempt:
                machines, _ = e2e_utils.exec_kubectl(  # pyright: ignore
                    args=[
                        "get", "machine", "-l", f"'{selector}'",
                        "--kubeconfig", self.mgmt_kubeconfig_path,
                        "-o", f"jsonpath=\"{{.items[?(@.status.phase == '{status}')].metadata.name}}\"",  # noqa:
                    ],
                    capture_output=True,
                    hide_cmd=True,
                )
                running_machines = machines.strip().split()  # pyright: ignore
                assert len(running_machines) == wanted_count, (
                    f"Expected {wanted_count} running CAPZ machines, "
                    f"but found {len(running_machines)}"
                )

    @e2e_utils.retry_on_error()
    def _setup_capz_kubeconfig(self):
        self.logging.info("Setting up CAPZ kubeconfig")

        output, _ = e2e_utils.run_shell_cmd(
            cmd=[
                "clusterctl", "get", "kubeconfig",
                "--kubeconfig", self.mgmt_kubeconfig_path,
                self.opts.cluster_name,
            ],
            capture_output=True,
        )
        with open(self.kubeconfig_path, 'w') as f:
            f.write(output.decode())
        os.chmod("/root/.kube/config", stat.S_IRUSR | stat.S_IWUSR)

        os.environ["KUBECONFIG"] = self.kubeconfig_path

    def _get_kubeadm_cluster_config(self):
        cfg_map = self.k8s_client.core_v1_api.read_namespaced_config_map(
            name="kubeadm-config", namespace="kube-system")
        return yaml.safe_load(cfg_map.data['ClusterConfiguration'])

    def _flannel_helm_values(self):
        helm_values = {
            "podCidr": self.opts.cluster_network_subnet,
            "flannel": {
                "backend": self.opts.flannel_mode,
            }
        }
        if self.opts.flannel_mode == e2e_constants.FLANNEL_MODE_OVERLAY:
            helm_values["flannel"]["vni"] = 4096
            helm_values["flannel"]["backendPort"] = 4789
        return helm_values

    def _install_flannel_helm(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml") as f:
            f.write(yaml.safe_dump(self._flannel_helm_values()).encode())
            f.flush()
            e2e_utils.run_shell_cmd(
                cmd=[
                    "helm", "install",
                    "--repo", e2e_constants.FLANNEL_HELM_REPO, "flannel",
                    "-n", e2e_constants.FLANNEL_NAMESPACE, "--values", f.name,
                    "--version", e2e_constants.FLANNEL_HELM_VERSION,
                    "--generate-name",
                ],
            )

    def _add_flannel_cni(self):
        e2e_utils.exec_kubectl([
            "create", "ns", e2e_constants.FLANNEL_NAMESPACE,
        ])
        e2e_utils.exec_kubectl([
            "label", "--overwrite", "ns",
            "kube-flannel", "pod-security.kubernetes.io/enforce=privileged",
        ])

        self._install_flannel_helm()

        cluster_config = self._get_kubeadm_cluster_config()
        context = {
            "cni_version": self.opts.cni_version,
            "container_image_tag": self.opts.container_image_tag,
            "container_image_registry": self.opts.container_image_registry,
            "control_plane_cidr": self.opts.control_plane_subnet_cidr_block,
            "node_cidr": self.opts.node_subnet_cidr_block,
            "service_subnet": cluster_config['networking']['serviceSubnet'],
            "pod_subnet": cluster_config['networking']['podSubnet'],
            "kubernetes_service_host": self.control_plane_public_address,
            "kubernetes_service_port": self.control_plane_public_port,
        }
        kube_flannel_windows = "/tmp/kube-flannel-windows.yaml"
        e2e_utils.render_template(
            template_file="kube-flannel-windows.yaml.j2",
            output_file=kube_flannel_windows,
            context=context,
            searchpath=f"{self.capz_flannel_dir}/flannel",
        )
        e2e_utils.exec_kubectl(["apply", "-f", kube_flannel_windows])

    def _get_latest_azure_cloud_provider_image_tag(self):
        api_tags = json.loads(
            e2e_utils.url_get(e2e_constants.CLOUD_PROVIDER_AZURE_TAGS))
        tags = [t["name"] for t in api_tags]
        tags.sort()
        return tags[-1]

    def _azure_cloud_provider_values(self, image_tag):
        helm_values = {
            "infra": {
                "clusterName": self.opts.cluster_name,
            },
            "cloudControllerManager": {
                "imageTag": image_tag,
                "clusterCIDR": self.opts.cluster_network_subnet,
                "configureCloudRoutes": False,
            },
            "cloudNodeManager": {
                "imageTag": image_tag,
            },
        }
        if self.opts.flannel_mode == e2e_constants.FLANNEL_MODE_L2BRIDGE:
            helm_values["cloudControllerManager"]["configureCloudRoutes"] = True  # noqa: E501
        return helm_values

    def _add_azure_cloud_provider(self):
        image_tag = self.kubernetes_version
        if "k8sbins" in self.bins_built:
            image_tag = self._get_latest_azure_cloud_provider_image_tag()
        repo = e2e_constants.CLOUD_PROVIDER_AZURE_HELM_REPO.format(image_tag)
        with tempfile.NamedTemporaryFile(suffix=".yaml") as f:
            f.write(
                yaml.safe_dump(
                    self._azure_cloud_provider_values(image_tag)
                ).encode()
            )
            f.flush()
            e2e_utils.run_shell_cmd(
                cmd=[
                    "helm", "install",
                    "--repo", repo,
                    "cloud-provider-azure",
                    "--generate-name", "--values", f.name,
                ],
            )

    def _setup_ssh_config(self):
        ssh_dir = os.path.join(os.environ["HOME"], ".ssh")
        ssh_config_file = os.path.join(ssh_dir, "config")

        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

        ssh_config = [
            f"Host {self.control_plane_public_address}",
            f"HostName {self.control_plane_public_address}",
            "User capi",
            "StrictHostKeyChecking no",
            "UserKnownHostsFile /dev/null",
            f"IdentityFile {self.ssh_private_key_path}",
            ""
        ]
        for address in (self.windows_private_addresses + self.linux_private_addresses):  # noqa:
            ssh_config += [
                f"Host {address}",
                f"HostName {address}",
                "User capi",
                f"ProxyCommand ssh -q {self.control_plane_public_address} -W %h:%p",  # noqa:
                "StrictHostKeyChecking no",
                "UserKnownHostsFile /dev/null",
                f"IdentityFile {self.ssh_private_key_path}",
                ""
            ]

        with open(ssh_config_file, "w") as f:
            f.write("\n".join(ssh_config))

    def _add_kube_proxy_windows(self):
        if ("k8sbins" not in self.bins_built) and (self.kubernetes_version != e2e_constants.DEFAULT_KUBERNETES_VERSION):  # noqa:
            # The kube-proxy bundled with the container image is different
            # than the one needed for this job run. So, we update it.
            for node_address in self.windows_private_addresses:
                self._run_node_cmd(
                    node_address=node_address,
                    cmd="mkdir -force /build",
                )
                self._run_node_cmd(
                    node_address=node_address,
                    cmd=f"curl.exe --fail -L -o /build/kube-proxy.exe https://dl.k8s.io/{self.kubernetes_version}/bin/windows/amd64/kube-proxy.exe",  # noqa:
                )
        context = {
            "cni_version": self.opts.cni_version,
            "k8s_bins": "k8sbins" in self.bins_built,
            "container_image_tag": self.opts.container_image_tag,
            "container_image_registry": self.opts.container_image_registry,
            "enable_win_dsr": str(self.opts.enable_win_dsr).lower(),
        }
        output_file = "/tmp/kube-proxy-windows.yaml"
        e2e_utils.render_template(
            template_file="kube-proxy-windows.yaml.j2",
            output_file=output_file,
            context=context,
            searchpath=f"{self.capz_flannel_dir}/kube-proxy",
        )
        e2e_utils.exec_kubectl(["apply", "-f", output_file])

    def _validate_k8s_api_versions(self):
        self.logging.info("Validating K8s API versions")

        nodes_yaml, _ = e2e_utils.exec_kubectl(  # pyright: ignore
            args=["get", "nodes", "-o", "yaml"],
            capture_output=True,
        )
        nodes = yaml.safe_load(nodes_yaml)  # pyright: ignore

        expected_ver = f"v{self._capz_sig_gallery_version_prefix()}"
        for node in nodes["items"]:
            node_name = node["metadata"]["name"]

            kubelet_ver: str = node["status"]["nodeInfo"]["kubeletVersion"]
            # relax the version match logic to within same minor version
            if not kubelet_ver.startswith(expected_ver):
                raise e2e_exceptions.VersionMismatch(
                    f"Wrong kubelet version on node {node_name}. "
                    f"Expected {expected_ver}, but found {kubelet_ver}")

            kube_proxy_ver: str = node["status"]["nodeInfo"]["kubeProxyVersion"]
            # Deprecated KubeProxy Version Reporting: https://github.com/kubernetes/kubernetes/commit/98c29f0312190904f55d62a4b4820fc17119ec10 
            if kube_proxy_ver != "" and not kube_proxy_ver.startswith(expected_ver):
                raise e2e_exceptions.VersionMismatch(
                    f"Wrong kube-proxy version on node {node_name}. "
                    f"Expected {expected_ver}, but found {kube_proxy_ver}")

    def _cleanup_capz_cluster(self):
        self._collect_bootstrap_vm_logs()

        self.logging.info("Deleting the mgmt cluster")
        self.bootstrap_vm.exec(["kind delete cluster"])

        self._delete_capz_rg(wait=True)
        self.bootstrap_vm.cleanup_vnet_peerings()

    def _conformance_nodes_non_blocking_taints(self):
        return [
            "node-role.kubernetes.io/control-plane",
        ]

    @e2e_utils.retry_on_error()
    def _build_k8s_linux_bins(self):
        self.logging.info("Building K8s Linux binaries")
        self.bootstrap_vm.exec(
            script=[
                'make WHAT="cmd/kubectl cmd/kubelet cmd/kubeadm" KUBE_BUILD_PLATFORMS="linux/amd64"',  # noqa:
            ],
            cwd=self.k8s_path)

    @e2e_utils.retry_on_error()
    def _build_k8s_windows_bins(self):
        self.logging.info("Building K8s Windows binaries")
        self.bootstrap_vm.exec(
            script=[
                'make WHAT="cmd/kubectl cmd/kubelet cmd/kubeadm cmd/kube-proxy" KUBE_BUILD_PLATFORMS="windows/amd64"',  # noqa:
            ],
            cwd=self.k8s_path)

    @e2e_utils.retry_on_error()
    def _build_k8s_linux_daemonset_images(self):
        self.logging.info("Building K8s Linux DaemonSet container images")
        self.bootstrap_vm.exec(
            script=[
                "KUBE_FASTBUILD=true KUBE_BUILD_CONFORMANCE=y make quick-release-images",  # noqa:
            ],
            cwd=self.k8s_path)

    def _copy_k8s_build_artifacts(self):
        self.logging.info("Copying K8s artifacts to their own directory")
        linux_bin_dir = f"{self.bootstrap_vm.artifacts_dir}/kubernetes/bin/linux/amd64"  # noqa:
        windows_bin_dir = f"{self.bootstrap_vm.artifacts_dir}/kubernetes/bin/windows/amd64"  # noqa:
        images_dir = f"{self.bootstrap_vm.artifacts_dir}/kubernetes/images"

        script = [f"mkdir -p {linux_bin_dir} {windows_bin_dir} {images_dir}"]

        for bin_name in ["kubectl", "kubelet", "kubeadm"]:
            linux_bin_path = "{}/{}/{}".format(
                self.k8s_path, "_output/local/bin/linux/amd64", bin_name
            )
            script.append(f"cp {linux_bin_path} {linux_bin_dir}")

        for bin_name in ["kubectl", "kubelet", "kubeadm", "kube-proxy"]:
            win_bin_path = "{}/{}/{}.exe".format(
                self.k8s_path, "_output/local/bin/windows/amd64", bin_name
            )
            script.append(f"cp {win_bin_path} {windows_bin_dir}")

        images_names = [
            "kube-apiserver.tar", "kube-controller-manager.tar",
            "kube-proxy.tar", "kube-scheduler.tar", "conformance-amd64.tar"
        ]
        for image_name in images_names:
            image_path = "{}/{}/{}".format(
                self.k8s_path, "_output/release-images/amd64", image_name
            )
            script.append(f"cp {image_path} {images_dir}")
        script.append(f"mv {images_dir}/conformance-amd64.tar {images_dir}/conformance.tar")  # noqa:
        script.append(f"chmod 644 {images_dir}/*")

        self.bootstrap_vm.exec(script)

    def _set_k8s_build_version(self):
        # Discover the K8s version built
        kubeadm_bin = os.path.join(
            self.k8s_path, "_output/local/bin/linux/amd64/kubeadm")
        stdout, _ = self.bootstrap_vm.exec(  # pyright: ignore
            script=[
                f"{kubeadm_bin} version -o=short",
            ],
            timeout=30,
            return_result=True)
        self.kubernetes_version = stdout.decode().strip()

    def _build_k8s_artifacts(self):
        self.bootstrap_vm.clone_git_repo(
            self.opts.k8s_repo,
            self.opts.k8s_branch,
            self.k8s_path,
        )
        self._build_k8s_linux_bins()
        self._build_k8s_windows_bins()
        self._build_k8s_linux_daemonset_images()
        self._set_k8s_build_version()
        self._copy_k8s_build_artifacts()

    def _copy_containerd_build_artifacts(self):
        self.logging.info(
            "Copying containerd binaries to artifacts directory")
        artifacts_containerd_bin_dir = os.path.join(
            self.bootstrap_vm.artifacts_dir, "containerd/bin"
        )
        script = [f"mkdir -p {artifacts_containerd_bin_dir}"]
        containerd_bins = os.path.join(self.containerd_path, "bin")
        script.append(
            "cp "
            f"{containerd_bins}/containerd.exe "
            f"{containerd_bins}/containerd-shim-runhcs-v1.exe "
            f"{containerd_bins}/containerd-stress.exe "
            f"{containerd_bins}/ctr.exe "
            f"{containerd_bins}/cri-tools/usr/local/bin/crictl.exe "
            f"{containerd_bins}/cri-tools/usr/local/bin/critest.exe "
            f"{artifacts_containerd_bin_dir}")
        self.bootstrap_vm.exec(script)

    @e2e_utils.retry_on_error()
    def _build_containerd_windows_bins(self):
        self.logging.info("Building containerd binaries")
        self.bootstrap_vm.exec(
            script=[
                "VERSION=1.7.0+unknown GOOS=windows make binaries",  # noqa: TODO(ibalutoiu): This hard-coded version needs to be removed once containerd v1.7.0 gets released.
                "GOOS=windows make -f Makefile.windows bin/containerd-shim-runhcs-v1.exe",  # noqa:
                "sudo GOOS=windows GOPATH=$HOME/go DESTDIR=$(pwd)/bin/cri-tools ./script/setup/install-critools",  # noqa:
            ],
            cwd=self.containerd_path)

    def _build_containerd_binaries(self):
        self.bootstrap_vm.clone_git_repo(
            self.opts.containerd_repo,
            self.opts.containerd_branch,
            self.containerd_path,
        )
        self._build_containerd_windows_bins()
        self._copy_containerd_build_artifacts()

    @e2e_utils.retry_on_error()
    def _build_containerd_shim_windows_bins(self):
        self.logging.info("Building containerd shim")
        self.bootstrap_vm.exec(
            script=[
                "GOOS=windows GO111MODULE=on go build -mod=vendor -o containerd-shim-runhcs-v1.exe ./cmd/containerd-shim-runhcs-v1",  # noqa:
            ],
            cwd=self.containerd_shim_path,
        )

    def _copy_containerd_shim_build_artifacts(self):
        self.logging.info(
            "Copying containerd-shim build to artifacts directory")
        artifacts_containerd_bin_dir = os.path.join(
            self.bootstrap_vm.artifacts_dir, "containerd-shim/bin"
        )
        script = [f"mkdir -p {artifacts_containerd_bin_dir}"]
        containerd_shim_bin = os.path.join(
            self.containerd_shim_path, "containerd-shim-runhcs-v1.exe")
        script.append(
            f"cp {containerd_shim_bin} {artifacts_containerd_bin_dir}")
        self.bootstrap_vm.exec(script)

    def _build_containerd_shim(self):
        self.bootstrap_vm.clone_git_repo(
            self.opts.containerd_shim_repo,
            self.opts.containerd_shim_branch,
            self.containerd_shim_path,
        )
        self._build_containerd_shim_windows_bins()
        self._copy_containerd_shim_build_artifacts()

    @e2e_utils.retry_on_error()
    def _build_cri_tools_windows_bins(self):
        self.logging.info("Building cri-tools")
        self.bootstrap_vm.exec(
            script=[
                "GOOS=windows make binaries",
            ],
            cwd=self.cri_tools_path)

    def _copy_cri_tools_build_artifacts(self):
        self.logging.info("Copying cri-tools build to artifacts directory")
        artifacts_cri_tools_bin_dir = os.path.join(
            self.bootstrap_vm.artifacts_dir, "cri-tools/bin"
        )
        script = [f"mkdir -p {artifacts_cri_tools_bin_dir}"]
        cri_tools_bins = os.path.join(self.cri_tools_path, "build/bin")
        script.append(
            "cp "
            f"{cri_tools_bins}/crictl.exe "
            f"{cri_tools_bins}/critest.exe "
            f"{artifacts_cri_tools_bin_dir}"
        )
        self.bootstrap_vm.exec(script)

    def _build_cri_tools(self):
        self.bootstrap_vm.clone_git_repo(
            self.opts.cri_tools_repo,
            self.opts.cri_tools_branch,
            self.cri_tools_path,
        )
        self._build_cri_tools_windows_bins()
        self._copy_cri_tools_build_artifacts()

    @e2e_utils.retry_on_error()
    def _build_sdn_cni_windows_bins(self):
        self.logging.info("Building the SDN CNI binaries")
        self.bootstrap_vm.exec(
            script=[
                "GOOS=windows make all",
            ],
            cwd=self.sdn_path)

    def _copy_sdn_cni_build_artifacts(self):
        self.logging.info("Copying SDN CNI binaries to artifacts directory")
        artifacts_cni_dir = os.path.join(
            self.bootstrap_vm.artifacts_dir, "cni/bin"
        )
        script = [f"mkdir -p {artifacts_cni_dir}"]
        for sdn_bin_name in ["nat.exe", "sdnbridge.exe", "sdnoverlay.exe"]:
            sdn_bin = os.path.join(self.sdn_path, "out", sdn_bin_name)
            script.append(f"cp {sdn_bin} {artifacts_cni_dir}")
        self.bootstrap_vm.exec(script)

    def _build_sdn_cni_binaries(self):
        self.bootstrap_vm.clone_git_repo(
            self.opts.sdn_repo,
            self.opts.sdn_branch,
            self.sdn_path
        )
        self._build_sdn_cni_windows_bins()
        self._copy_sdn_cni_build_artifacts()
