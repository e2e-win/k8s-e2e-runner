import configargparse
import constants
import datetime
import deployer
import log
import utils
import os
import random
import subprocess
import json
import urlparse
from azure.common.credentials import ServicePrincipalCredentials
from azure.mgmt.resource import ResourceManagementClient

p = configargparse.get_argument_parser()

p.add("--location", default=None, help="Resource group location.")
p.add("--rg_name", help="resource group name.")
p.add("--master-vm-name", help="Name of master vm.")
p.add("--master-vm-size", default="Standard_D2s_v3", help="Size of master vm")

p.add("--win-minion-count", type=int, default=2, help="Number of windows minions for the deployment.")
p.add("--win-minion-name-prefix", default="winvm", help="Prefix for win minion vm names.")
p.add("--win-minion-size", default="Standard_D2s_v3", help="Size of minion vm")
p.add("--win-minion-password", default=None, help="Password for win minion vm")
p.add("--win-minion-image", default="MicrosoftWindowsServer:WindowsServer:Datacenter-Core-1809-with-Containers-smalldisk:17763.973.2001110547", help="Windows image SKU or custom vhd path")
p.add("--terraform-config")
p.add("--ssh-public-key-path", default=os.path.join(os.path.join(os.getenv("HOME"), ".ssh", "id_rsa.pub")))
p.add("--ssh-private-key-path", default=os.path.join(os.path.join(os.getenv("HOME"), ".ssh", "id_rsa")))


class TerraformProvisioner(deployer.NoopDeployer):

    CUSTOM_VHD = "custom_vhd"
    AZURE_IMAGE_URN = "image_urn"

    def __init__(self):
        self.opts = p.parse_known_args()[0]
        if self.opts.location is None:
            self.opts.location = random.choice(constants.AZURE_LOCATIONS)

        self.cluster = self._generate_cluster()
        self.terraform_config_url = self.opts.terraform_config

        self.terraform_root = "/tmp/terraform_root"
        self.terraform_config_path = os.path.join(self.terraform_root, "terraform.tf")
        self.terraform_vars_file = os.path.join(self.terraform_root, "terraform.tfvars")
        self.win_minion_password = None

        self.logging = log.getLogger(__name__)

        self._generate_cluster()
        self._create_terraform_root()

    def _generate_cluster(self):
        cluster = {}

        cluster["location"] = self.opts.location
        cluster["resource_group"] = self.opts.rg_name
        cluster["master_vm"] = dict(vm_name=self.opts.master_vm_name, vm_size=self.opts.master_vm_size, public_ip=None)
        cluster["win_vms"] = dict(win_vm_name_prefix=self.opts.win_minion_name_prefix,
                                  win_vm_count=self.opts.win_minion_count,
                                  win_vm_size=self.opts.win_minion_size,
                                  vms=[])
        for index in range(cluster["win_vms"]["win_vm_count"]):
            vm_name = self.opts.win_minion_name_prefix + str(index)
            cluster["win_vms"]["vms"].append(dict(vm_name=vm_name))

        return cluster

    def get_cluster_location(self):
        return self.cluster["location"]

    def get_cluster_rg_name(self):
        return self.cluster["resource_group"]

    def get_cluster_rg_build_id(self):
        return os.getenv("BUILD_ID").strip()

    def get_cluster_rg_creation_timestamp(self):
        return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    def get_cluster_rg_job_name(self):
        return os.getenv("JOB_NAME").strip()

    def get_cluster_master_vm_name(self):
        return self.cluster["master_vm"]["vm_name"]

    def get_cluster_master_public_ip(self):
        return self.cluster["master_vm"]["public_ip"]

    def _set_cluster_master_vm_public_ip(self, master_public_ip):
        self.cluster["master_vm"]["public_ip"] = master_public_ip

    def get_cluster_min_private_ip(self, vm_name):
        for vm in self.cluster["win_vms"]["vms"]:
            if vm["vm_name"] == vm_name:
                return vm["private_ip"]

    def _set_cluster_win_min_private_ip(self, vm_name, vm_private_ip):
        for vm in self.cluster["win_vms"]["vms"]:
            if vm["vm_name"] == vm_name:
                vm["private_ip"] = vm_private_ip

    def get_cluster_master_vm_size(self):
        return self.cluster["master_vm"]["vm_size"]

    def get_cluster_master_vm(self):
        return self.cluster["master_vm"]

    def get_cluster_win_minion_vm_prefix(self):
        return self.cluster["win_vms"]["win_vm_name_prefix"]

    def get_cluster_win_minion_vm_count(self):
        return self.cluster["win_vms"]["win_vm_count"]

    def get_cluster_win_minion_vm_size(self):
        return self.cluster["win_vms"]["win_vm_size"]

    def get_cluster_win_minion_vms(self):
        return self.cluster["win_vms"]["vms"]

    def get_cluster_win_minion_vms_names(self):
        return [vm["vm_name"] for vm in self.get_cluster_win_minion_vms()]

    def get_all_vms(self):
        vms = []
        vms.append(self.get_cluster_master_vm())
        vms.extend(self.get_cluster_win_minion_vms)

        return vms

    def _create_terraform_root(self):
        utils.rm_dir(self.terraform_root)
        utils.mkdir_p(self.terraform_root)

    def _get_terraform_config(self):
        self.logging.info("Downloading terraform config.")
        utils.download_file(self.terraform_config_url, self.terraform_config_path)

    def _get_ssh_public_key(self, key_file):
        if not os.path.exists(key_file):
            msg = ("Unable to find ssh key %s. No such path exists." % key_file)
            self.logging.error(msg)
            raise Exception(msg)

        with open(key_file, "r") as f:
            pub_key = f.read().strip()

        return pub_key

    def get_win_vm_password(self):
        if self.opts.win_minion_password is None:
            if self.win_minion_password is None:
                ssh_public_key = self._get_ssh_public_key(self.opts.ssh_public_key_path)
                self.win_minion_password = utils.generate_random_password(key=ssh_public_key)
        else:
            self.win_minion_password = self.opts.win_minion_password

        return self.win_minion_password

    def get_win_vm_username(self):
        return constants.WINDOWS_ADMIN_USER

    def get_win_vm_port(self, vm_name):
        return constants.tunnel_ports[vm_name]

    def get_master_username(self):
        return constants.WINDOWS_ADMIN_USER

    def _is_url(self, image):
        return urlparse.urlparse(image).scheme in ("http", "https")

    def _is_azure_image_urn(self, image):
        return len(image.split(":")) == 4

    def _parse_azure_storage_account(self, image_url):
        return urlparse.urlparse(image_url).netloc.split(".")[0]

    def _parse_azure_image_urn(self, image_urn):
        image = {}
        split_urn = image_urn.split(":")
        image["win_img_publisher"] = split_urn[0]
        image["win_img_offer"] = split_urn[1]
        image["win_img_sku"] = split_urn[2]
        image["win_img_version"] = split_urn[3]
        return image

    def _get_win_minion_image_type(self, image):
        if self._is_url(image):
            return TerraformProvisioner.CUSTOM_VHD
        if self._is_azure_image_urn(image):
            return TerraformProvisioner.AZURE_IMAGE_URN
        return None

    def _create_terraform_vars_file(self):
        self.logging.info("Creating terraform vars file.")
        out_format = '%s = "%s"\n'
        ssh_public_key = self._get_ssh_public_key(self.opts.ssh_public_key_path)

        win_min_image_type = self._get_win_minion_image_type(self.opts.win_minion_image)

        if win_min_image_type is None:
            msg = "Unrecognized image string: %s" % self.opts.win_minion_image
            self.logging.error(msg)
            raise Exception(msg)

        extra_args = ""
        if win_min_image_type == TerraformProvisioner.CUSTOM_VHD:
            extra_args = extra_args + (out_format % ("win_img_uri", self.opts.win_minion_image))
            extra_args = extra_args + (out_format % ("win_img_storage_account", self._parse_azure_storage_account(self.opts.win_minion_image)))
        if win_min_image_type == TerraformProvisioner.AZURE_IMAGE_URN:
            image = self._parse_azure_image_urn(self.opts.win_minion_image)
            for key in image.keys():
                extra_args = extra_args + (out_format % (key, image[key]))

        with open(self.terraform_vars_file, "w") as f:
            f.write(out_format % ("location", self.get_cluster_location()))
            f.write(out_format % ("rg_name", self.get_cluster_rg_name()))
            f.write(out_format % ("rg_build_id", self.get_cluster_rg_build_id()))
            f.write(out_format % ("rg_creation_timestamp", self.get_cluster_rg_creation_timestamp()))
            f.write(out_format % ("rg_job_name", self.get_cluster_rg_job_name()))
            f.write(out_format % ("master_vm_name", self.get_cluster_master_vm_name()))
            f.write(out_format % ("master_vm_size", self.get_cluster_master_vm_size()))
            f.write(out_format % ("win_minion_count", self.get_cluster_win_minion_vm_count()))
            f.write(out_format % ("win_minion_vm_size", self.get_cluster_win_minion_vm_size()))
            f.write(out_format % ("win_minion_vm_name_prefix", self.get_cluster_win_minion_vm_prefix()))
            f.write(out_format % ("win_minion_vm_username", self.get_win_vm_username()))
            f.write(out_format % ("win_minion_vm_password", self.get_win_vm_password()))
            f.write(out_format % ("ssh_key_data", ssh_public_key))
            f.write(extra_args)

    def _get_terraform_vars_azure(self):
        cmd = []
        msg = "Env var %s not set."
        env_vars = ["AZURE_SUB_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]
        terraform_vars = ["azure_sub_id", "azure_client_id", "azure_client_secret", "azure_tenant_id"]

        for terraform_var, env_var in zip(terraform_vars, env_vars):
            if not os.getenv(env_var):
                self.logging.error(msg % env_var)
                raise Exception(msg % env_var)
            cmd.append("-var")
            var = ("'%s=%s'" % (terraform_var, os.getenv(env_var).strip()))
            cmd.append(var)

        return cmd

    def _get_terraform_apply_cmd(self):
        cmd = ["terraform", "apply", "-auto-approve"]

        cmd.extend(self._get_terraform_vars_azure())
        cmd.append(".")

        return cmd

    def _deploy_cluster(self):
        self.logging.info("Deploying cluster")

        self.logging.info("Init terraform.")
        cmd = ["terraform", "init"]

        _, err, ret = utils.run_cmd(cmd, stderr=True, cwd=self.terraform_root)
        if ret != 0:
            msg = "Failed to init terraform with error: %s" % err
            self.logging.error(msg)
            raise Exception(msg)

        cmd = self._get_terraform_apply_cmd()
        out, err, ret = utils.run_cmd(cmd, stdout=True, stderr=True, cwd=self.terraform_root, shell=True, sensitive=True)
        if ret != 0:
            msg = "Failed to apply terraform config with error: %s" % err
            self.logging.error(msg)
            raise Exception(msg)

        cmd = ["terraform", "output", "-json"]
        out, err, ret = utils.run_cmd(cmd, stdout=True, stderr=True, cwd=self.terraform_root)

        return json.loads(out)

    def _parse_terraform_output(self, output):
        master_ip = output["master"]["value"][self.get_cluster_master_vm_name()]
        self._set_cluster_master_vm_public_ip(master_ip)
        for vm_name, vm_prv_ip in output["privateips"]["value"].items():
            self._set_cluster_win_min_private_ip(vm_name, vm_prv_ip)

        print json.dumps(self.cluster)

    def _populate_hosts_file(self):
        with open("/etc/hosts", "a") as f:
            vm_name = self.get_cluster_master_vm_name()
            vm_name = vm_name + " kubernetes"
            vm_public_ip = self.get_cluster_master_public_ip()
            hosts_entry = ("%s %s\n" % (vm_public_ip, vm_name))
            self.logging.info("Adding entry '%s' to hosts file." % hosts_entry.rstrip("\n"))
            f.write(hosts_entry)

    def up(self):
        self.logging.info("Terraform up.")
        self._get_terraform_config()
        self._create_terraform_vars_file()
        terraform_output = self._deploy_cluster()
        self._parse_terraform_output(terraform_output)
        self._populate_hosts_file()

    def down(self):
        # Unfortunately, terraform destroy is not working properly
        # Destroy will be handled via SDK calls
        self.logging.info("Az destroy rgdel")

        try:
            credentials = ServicePrincipalCredentials(
                client_id=os.environ['AZURE_CLIENT_ID'].strip(),
                secret=os.environ['AZURE_CLIENT_SECRET'].strip(),
                tenant=os.environ['AZURE_TENANT_ID'].strip()
            )
            subscription_id = os.environ.get(
                'AZURE_SUB_ID',
                '11111111-1111-1111-1111-111111111111'
            ).strip()
            client = ResourceManagementClient(credentials, subscription_id)
            delete_async_operation = client.resource_groups.delete(self.opts.rg_name)
            delete_async_operation.wait()
        except Exception as e:
            # Should find specific exception for when RG is not found
            self.logging.error("Failed to destroy rgdel with error: %s", e)
