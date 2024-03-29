import os
import random
import string
import time

import azure.mgmt.containerservice.models as aks_models
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient
from e2e_runner import base as e2e_base
from e2e_runner import exceptions as e2e_exceptions
from e2e_runner import logger as e2e_logger
from e2e_runner.utils import azure as e2e_azure_utils
from e2e_runner.utils import utils as e2e_utils


class AksCI(e2e_base.CI):

    def __init__(self, opts):
        super(AksCI, self).__init__(opts)

        self.aks_dir = os.path.dirname(__file__)
        self.logging = e2e_logger.get_logger(__name__)
        self.artifacts_directory = self.opts.artifacts_directory

        self.aks_name = self.opts.cluster_name
        self.rg_name = self.aks_name
        self.tags = e2e_azure_utils.get_resource_group_tags()
        self.linux_pool_name = "linagt"
        self.linux_agents_count = self.opts.linux_agents_count
        self.linux_agents_size = self.opts.linux_agents_size
        self.win_pool_name = "winagt"
        self.win_agents_count = self.opts.win_agents_count
        self.win_agents_size = self.opts.win_agents_size
        self.win_agents_sku = self.opts.win_agents_sku
        self.ssh_public_key = e2e_utils.get_file_content(
            os.environ["SSH_PUBLIC_KEY_PATH"])

        creds, sub_id = e2e_azure_utils.get_credentials()
        self.mgmt_client = ResourceManagementClient(creds, sub_id)
        self.aks_client = ContainerServiceClient(creds, sub_id)
        self.network_client = NetworkManagementClient(creds, sub_id)
        self.compute_client = ComputeManagementClient(creds, sub_id)

        self.location = self._get_location()
        self.aks_version = self._get_latest_aks_patch(
            self.location, self.opts.aks_version)
        self.kubernetes_version = f"v{self.aks_version}"

    @property
    def windows_private_addresses(self):
        addresses = e2e_utils.get_k8s_agents_private_addresses("windows")
        if len(addresses) != self.win_agents_count:
            raise e2e_exceptions.KubernetesNodeNotFound(
                f"Expected {self.win_agents_count} Windows agents "
                f"addresses, but found only {len(addresses)}")
        return addresses

    def up(self):
        start = time.time()
        self._create_metadata_artifact()
        self._setup_aks_cluster()
        self._setup_aks_kubeconfig()
        self.logging.info("The cluster provisioned in %.2f minutes",
                          (time.time() - start) / 60)

    def down(self):
        self.logging.info("Deleting AKS cluster resource group")
        e2e_azure_utils.delete_resource_group(
            self.mgmt_client, self.rg_name, wait=False)

    def collect_logs(self):
        self._setup_jumpbox()

        nodes_names = self._get_k8s_nodes_names("windows")
        for node_name in nodes_names:
            try:
                self._collect_node_logs(node_name)
            except Exception as e:
                self.logging.warning(
                    "Failed to collect logs from node %s: %s", node_name, e)

    def _get_location(self):
        location = self.opts.location
        if not location:
            location = e2e_azure_utils.get_least_used_location(
                self.compute_client, self.network_client)
        self.logging.info("Using Azure location %s", location)
        return location

    @e2e_utils.retry_on_error()
    def _get_latest_aks_patch(self, location, aks_version):
        versions = [
            i.orchestrator_version
            for i in self.aks_client.container_services.list_orchestrators(
                location=location,
                resource_type='managedClusters').orchestrators
            if i.orchestrator_version.startswith(aks_version)
        ]
        if len(versions) == 0:
            raise e2e_exceptions.KubernetesVersionNotFound(
                "Didn't find any AKS version patch corresponding to "
                f"orchestrator version {aks_version}"
            )
        patches = [int(v.split(".")[-1]) for v in versions]
        patches.sort()
        version_split = aks_version.split(".")
        version = f"{version_split[0]}.{version_split[1]}.{patches[-1]}"
        self.logging.info("Using AKS version: %s", version)
        return version

    def _generate_win_admin_pass(self):
        special_chars = "+-.<=>@_"
        pass_chars = string.ascii_letters + string.digits + special_chars
        password = ''.join((random.choice(pass_chars) for i in range(32)))
        with open("windows_admin_pass.txt", "w") as f:
            f.write(password)
            return password

    def _get_sp_profile(self):
        return aks_models.ManagedClusterServicePrincipalProfile(
            client_id=os.environ["AZURE_CLIENT_ID"],
            secret=os.environ["AZURE_CLIENT_SECRET"],
        )

    def _get_linux_agents_taints(self):
        return [
            "CriticalAddonsOnly=true:NoSchedule",
        ]

    def _get_linux_agents_profile(self):
        return aks_models.ManagedClusterAgentPoolProfile(
            name=self.linux_pool_name,
            count=self.linux_agents_count,
            vm_size=self.linux_agents_size,
            os_type=aks_models.OSType.LINUX,
            os_sku=aks_models.OSSKU.UBUNTU,
            type=aks_models.AgentPoolType.VIRTUAL_MACHINE_SCALE_SETS,
            mode=aks_models.AgentPoolMode.SYSTEM,
            node_taints=self._get_linux_agents_taints(),
            orchestrator_version=self.aks_version,
            os_disk_size_gb=128,
            tags=self.tags,
        )

    def _get_windows_agents_profile(self):
        return aks_models.ManagedClusterAgentPoolProfile(
            name=self.win_pool_name,
            count=self.win_agents_count,
            vm_size=self.win_agents_size,
            os_type=aks_models.OSType.WINDOWS,
            os_sku=self.win_agents_sku,
            type=aks_models.AgentPoolType.VIRTUAL_MACHINE_SCALE_SETS,
            orchestrator_version=self.aks_version,
            os_disk_size_gb=128,
            tags=self.tags,
        )

    def _get_aks_cluster(self):
        return aks_models.ManagedCluster(
            location=self.location,
            kubernetes_version=self.aks_version,
            node_resource_group=f"{self.aks_name}-node-rg",
            dns_prefix=self.aks_name,
            enable_rbac=True,
            agent_pool_profiles=[
                self._get_linux_agents_profile(),
                self._get_windows_agents_profile(),
            ],
            service_principal_profile=self._get_sp_profile(),
            linux_profile=aks_models.ContainerServiceLinuxProfile(
                admin_username="azureuser",
                ssh=aks_models.ContainerServiceSshConfiguration(
                    public_keys=[
                        aks_models.ContainerServiceSshPublicKey(
                            key_data=self.ssh_public_key,
                        ),
                    ],
                ),
            ),
            windows_profile=aks_models.ManagedClusterWindowsProfile(
                admin_username="azureuser",
                admin_password=self._generate_win_admin_pass(),
            ),
            network_profile=aks_models.ContainerServiceNetworkProfile(
                network_plugin="azure",
                network_policy="azure",
                ip_families=[
                    aks_models.IpFamily.I_PV4,
                ],
            ),
            tags=self.tags,
        )

    @e2e_utils.retry_on_error()
    def _setup_aks_cluster(self):
        try:
            self.logging.info("Creating the AKS resource group")
            e2e_azure_utils.create_resource_group(
                client=self.mgmt_client,
                name=self.rg_name,
                location=self.location,
                tags=self.tags)
            self.logging.info("Creating the AKS cluster")
            self.aks_client.managed_clusters.begin_create_or_update(
                resource_group_name=self.rg_name,
                resource_name=self.aks_name,
                parameters=self._get_aks_cluster()).wait()  # pyright: ignore
        except Exception as ex:
            self.logging.info("Deleting AKS resource group")
            e2e_azure_utils.delete_resource_group(
                self.mgmt_client, self.rg_name, wait=True)
            raise ex

    @e2e_utils.retry_on_error()
    def _setup_aks_kubeconfig(self):
        cfgs = self.aks_client.managed_clusters.list_cluster_user_credentials(
            self.rg_name, self.aks_name).kubeconfigs
        with open(self.kubeconfig_path, "w") as f:
            f.write(cfgs[0].value.decode(encoding='UTF-8'))  # pyright: ignore
        os.environ["KUBECONFIG"] = self.kubeconfig_path

    def _conformance_nodes_non_blocking_taints(self):
        linux_agents_taints = [
            taint.split("=")[0] for taint in self._get_linux_agents_taints()
        ]
        return linux_agents_taints

    def _collect_node_logs(self, node_name):
        self.logging.info("Collecting logs from node: %s", node_name)

        node_address = self._get_k8s_node_private_address(node_name)
        script_file = "collect-logs.ps1"
        ps_script_path = os.path.join(
            self.e2e_runner_dir, f"scripts/aks/{script_file}")
        ssh_kwargs = {
            "user": "azureuser",
            "address": node_address,
        }

        e2e_utils.upload_to_pod(
            self.JUMPBOX_POD,
            ps_script_path,
            f"/tmp/{script_file}")
        self._jumpbox_scp_upload(
            file_path=f"/tmp/{script_file}",
            remote_file_path=f"/{script_file}",
            **ssh_kwargs,
        )
        self._jumpbox_exec_ssh(
            cmd=["powershell", "-File", f"/{script_file}"],
            **ssh_kwargs,
        )
        self._jumpbox_scp_download(
            remote_file_path="/logs.zip",
            file_path=f"/tmp/{node_name}_logs.zip",
            **ssh_kwargs,
        )
        e2e_utils.download_from_pod(
            pod_name=self.JUMPBOX_POD,
            remote_path=f"/tmp/{node_name}_logs.zip",
            local_path=f"{self.artifacts_directory}/{node_name}_logs.zip",
        )
