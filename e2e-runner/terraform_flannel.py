import ast
import json
import glob
import os
import shutil
import time
import yaml

import configargparse

import ci
import constants
import utils
import terraform

p = configargparse.get_argument_parser()

p.add("--ansibleRepo",
      default="https://github.com/e2e-win/flannel-kubernetes",
      help="Ansible Repository for ovn-ovs playbooks.")
p.add("--ansibleBranch",
      default="master",
      help="Ansible Repository branch for ovn-ovs playbooks.")
p.add("--flannelMode", default="overlay", help="Option: overlay or host-gw")
p.add("--containerRuntime",
      default="docker",
      help="Container runtime to set in ansible: docker / containerd.")
p.add("--remoteCmdRetries",
      type=int,
      default=5,
      help="Number of retries Ansible adhoc command should do.")
p.add("--terraformData", default="", help="Data from terraform cluster.")


class Terraform_Flannel(ci.CI):

    DEFAULT_ANSIBLE_PATH = "/tmp/flannel-kubernetes"
    ANSIBLE_PLAYBOOK = "kubernetes-cluster.yml"
    ANSIBLE_PLAYBOOK_ROOT = DEFAULT_ANSIBLE_PATH
    ANSIBLE_HOSTS_TEMPLATE = ("[kube-master]\n"
                              "KUBE_MASTER_PLACEHOLDER\n\n"
                              "[kube-minions-windows]\n"
                              "KUBE_MINIONS_WINDOWS_PLACEHOLDER\n")
    ANSIBLE_HOSTS_PATH = "%s/inventory/hosts" % ANSIBLE_PLAYBOOK_ROOT
    DEFAULT_ANSIBLE_WINDOWS_ADMIN = "Admin"
    DEFAULT_ANSIBLE_HOST_VAR_WINDOWS_TEMPLATE = (
        'ansible_user: "USERNAME_PLACEHOLDER"\n'
        'ansible_password: "PASS_PLACEHOLDER"\n'
        'ansible_winrm_read_timeout_sec: 240\n')
    DEFAULT_ANSIBLE_HOST_VAR_DIR = "%s/inventory/host_vars" % (
        ANSIBLE_PLAYBOOK_ROOT)
    DEFAULT_GROUP_VARS_PATH = "%s/inventory/group_vars/all" % (
        ANSIBLE_PLAYBOOK_ROOT)
    HOSTS_FILE = "/etc/hosts"
    ANSIBLE_CONFIG_FILE = "%s/ansible.cfg" % ANSIBLE_PLAYBOOK_ROOT

    KUBE_CONFIG_PATH = "/root/.kube/config"
    KUBE_TLS_SRC_PATH = "/etc/kubernetes/tls/"

    FLANNEL_MODE_OVERLAY = "overlay"
    FLANNEL_MODE_L2BRIDGE = "host-gw"

    AZURE_CCM_LOCAL_PATH = "/tmp/azure.json"
    AZURE_CONFIG_TEMPALTE = {
        "cloud": "AzurePublicCloud",
        "tenantId": "",
        "subscriptionId": "",
        "aadClientId": "",
        "aadClientSecret": "",
        "resourceGroup": "",
        "location": "",
        "subnetName": "clusterSubnet",
        "securityGroupName": "masterNSG",
        "vnetName": "clusterNet",
        "vnetResourceGroup": "",
        "routeTableName": "routeTable",
        "primaryAvailabilitySetName": "",
        "primaryScaleSetName": "",
        "cloudProviderBackoff": True,
        "cloudProviderBackoffRetries": 6,
        "cloudProviderBackoffExponent": 1.5,
        "cloudProviderBackoffDuration": 5,
        "cloudProviderBackoffJitter": 1,
        "cloudProviderRatelimit": True,
        "cloudProviderRateLimitQPS": 3,
        "cloudProviderRateLimitBucket": 10,
        "useManagedIdentityExtension": False,
        "userAssignedIdentityID": "",
        "useInstanceMetadata": True,
        "loadBalancerSku": "Basic",
        "excludeMasterFromStandardLB": False,
        "providerVaultName": "",
        "maximumLoadBalancerRuleCount": 250,
        "providerKeyName": "k8s",
        "providerKeyVersion": ""
    }

    def __init__(self):
        super(Terraform_Flannel, self).__init__()

        self.deployer = terraform.TerraformProvisioner()

        self.default_ansible_path = Terraform_Flannel.DEFAULT_ANSIBLE_PATH
        self.ansible_playbook = Terraform_Flannel.ANSIBLE_PLAYBOOK
        self.ansible_playbook_root = Terraform_Flannel.ANSIBLE_PLAYBOOK_ROOT
        self.ansible_hosts_template = Terraform_Flannel.ANSIBLE_HOSTS_TEMPLATE
        self.ansible_hosts_path = Terraform_Flannel.ANSIBLE_HOSTS_PATH
        self.ansible_windows_admin = \
            Terraform_Flannel.DEFAULT_ANSIBLE_WINDOWS_ADMIN
        self.ansible_host_var_windows_template = \
            Terraform_Flannel.DEFAULT_ANSIBLE_HOST_VAR_WINDOWS_TEMPLATE
        self.ansible_host_var_dir = \
            Terraform_Flannel.DEFAULT_ANSIBLE_HOST_VAR_DIR
        self.ansible_config_file = Terraform_Flannel.ANSIBLE_CONFIG_FILE
        self.ansible_group_vars_file = \
            Terraform_Flannel.DEFAULT_GROUP_VARS_PATH
        self.patches = None

    def set_patches(self, patches=None):
        self.patches = patches

    def _generate_azure_config(self):
        azure_config = Terraform_Flannel.AZURE_CONFIG_TEMPALTE
        azure_config["tenantId"] = os.getenv("AZURE_TENANT_ID").strip()
        azure_config["subscriptionId"] = os.getenv("AZURE_SUB_ID").strip()
        azure_config["aadClientId"] = os.getenv("AZURE_CLIENT_ID").strip()
        azure_config["aadClientSecret"] = os.getenv(
            "AZURE_CLIENT_SECRET").strip()

        azure_config["resourceGroup"] = self.opts.rg_name
        azure_config["location"] = self.opts.location
        azure_config["vnetResourceGroup"] = self.opts.rg_name

        with open(Terraform_Flannel.AZURE_CCM_LOCAL_PATH, "w") as f:
            f.write(json.dumps(azure_config))

    def _prepare_ansible(self):
        utils.clone_repo(self.opts.ansibleRepo, self.opts.ansibleBranch,
                         self.default_ansible_path)

        # Creating ansible hosts file
        linux_master_hostname = self.deployer.get_cluster_master_vm_name()
        windows_minions_hostnames = \
            self.deployer.get_cluster_win_minion_vms_names()

        hosts_file_content = self.ansible_hosts_template.replace(
            "KUBE_MASTER_PLACEHOLDER", linux_master_hostname)
        hosts_file_content = hosts_file_content.replace(
            "KUBE_MINIONS_WINDOWS_PLACEHOLDER",
            "\n".join(windows_minions_hostnames))

        self.logging.info("Writing hosts file for ansible inventory.")
        with open(self.ansible_hosts_path, "w") as f:
            f.write(hosts_file_content)

        # This proliferation of args should be set to cli
        # to ansible when called.
        win_hosts_extra_vars = "\nCONTAINER_RUNTIME: \"%s\"" % (
            self.opts.containerRuntime)
        if self.opts.containerRuntime == "containerd":
            win_hosts_extra_vars += "\nCNIBINS: \"sdnms\""

        # Creating hosts_vars for hosts
        for vm_name in windows_minions_hostnames:
            vm_username = self.deployer.get_win_vm_username()
            vm_pass = self.deployer.get_win_vm_password()
            hosts_var_content = self.ansible_host_var_windows_template.replace(
                "USERNAME_PLACEHOLDER",
                vm_username).replace("PASS_PLACEHOLDER", vm_pass)
            filepath = os.path.join(self.ansible_host_var_dir, vm_name)
            with open(filepath, "w") as f:
                f.write(hosts_var_content)
                f.write(win_hosts_extra_vars)

        # Enable ansible log, json output and set ssh options
        with open(self.ansible_config_file, "a") as f:
            log_file = os.path.join(self.opts.log_path, "ansible-deploy.log")
            log_config = "log_path=%s\n" % log_file
            json_output = ("stdout_callback = json\n"
                           "bin_ansible_callbacks = True")
            # This probably goes better in /etc/ansible.cfg (set in dockerfile)
            ansible_config = ("\n\n[ssh_connection]\n"
                              "ssh_args="
                              "-o StrictHostKeyChecking=no "
                              "-o UserKnownHostsFile=/dev/null\n")
            f.write(log_config)
            f.write(json_output)
            f.write(ansible_config)

        full_ansible_tmp_path = os.path.join(self.ansible_playbook_root, "tmp")
        utils.mkdir_p(full_ansible_tmp_path)
        # Copy prebuilt binaries to ansible tmp
        for path in glob.glob("%s/*" % utils.get_bins_path()):
            self.logging.info("Copying %s to %s.", path, full_ansible_tmp_path)
            shutil.copy(path, full_ansible_tmp_path)

        azure_ccm = "false"
        # Generate azure.json if needed and populate group vars with
        # necessary paths
        if self.opts.flannelMode == Terraform_Flannel.FLANNEL_MODE_L2BRIDGE:
            self._generate_azure_config()
            azure_ccm = "true"

        # Set flannel mode in group vars
        with open(self.ansible_group_vars_file, "a") as f:
            f.write("FLANNEL_MODE: %s\n" % self.opts.flannelMode)
            f.write("AZURE_CCM: %s\n" % azure_ccm)
            f.write("AZURE_CCM_LOCAL_PATH: %s\n" %
                    Terraform_Flannel.AZURE_CCM_LOCAL_PATH)

    def _add_ssh_key(self):
        self.logging.info("Adding SSH key.")
        vms = self.deployer.get_cluster_win_minion_vms_names()
        self._runRemoteCmd(
            ("mkdir C:\\\\Users\\\\%s\\\\.ssh" % constants.WINDOWS_ADMIN_USER),
            vms,
            self.opts.remoteCmdRetries,
            windows=True)
        self._copyTo(self.opts.ssh_public_key_path,
                     ("C:\\\\Users\\\\%s\\\\.ssh\\\\authorized_keys" %
                      constants.WINDOWS_ADMIN_USER),
                     vms,
                     windows=True)

    def _install_patches(self):
        self.logging.info("Installing patches.")
        installer_script = os.path.join(os.getcwd(), "installPatches.ps1")
        vms = self.deployer.get_cluster_win_minion_vms_names()

        self._copyTo(installer_script, "c:\\", vms, windows=True)
        self._runRemoteCmd(("c:\\installPatches.ps1 %s" % self.patches),
                           vms,
                           self.opts.remoteCmdRetries,
                           windows=True,
                           root=True)

        ret, out = self._waitForConnection(vms, windows=True)
        if ret != 0:
            self.logging.error("No connection to machines. Error: %s", out)
            raise Exception("No connection to machines. Error: %s" % out)

    def install_lanfix(self):
        utils.mkdir_p("/tmp/lanfix")
        fix_files = ["vfpext.sys", "vfpext.sys.signinfo", "sfpcopy.exe"]
        vms = self.deployer.get_cluster_win_minion_vms_names()

        for file in fix_files:
            self.logging.info("Downloading file: %s", file)
            ret = utils.download_file(("http://10.0.10.187/%s" % file),
                                      ("/tmp/lanfix/%s" % file))
            if ret != 0:
                raise Exception("Failed to download file: %s" % file)
            self._copyTo(("/tmp/lanfix/%s" % file), "c:\\", vms, windows=True)

        self._runRemoteCmd(("bcdedit /set testsigning on"),
                           vms,
                           self.opts.remoteCmdRetries,
                           windows=True)
        self._runRemoteCmd(("C:\\sfpcopy.exe "
                            "C:\\vfpext.sys "
                            "C:\\Windows\\System32\\drivers\\vfpext.sys"),
                           vms,
                           self.opts.remoteCmdRetries,
                           windows=True)
        self._runRemoteCmd(("Restart-Computer"),
                           vms,
                           self.opts.remoteCmdRetries,
                           windows=True)
        self._waitForConnection(vms, windows=True)

    def _deploy_ansible(self):
        self.logging.info("Starting Ansible deployment.")
        cmd = "ansible-playbook %s -v" % self.ansible_playbook
        cmd = cmd.split()
        cmd.append("--key-file=%s" % self.opts.ssh_private_key_path)

        out, _, ret = utils.run_cmd(cmd,
                                    stdout=True,
                                    cwd=self.ansible_playbook_root)
        out = out.decode('ascii').strip()

        if ret != 0:
            self.logging.error(
                "Failed to deploy ansible-playbook with error: %s", out)
            raise Exception(
                "Failed to deploy ansible-playbook with error: %s" % out)
        self.logging.info("Succesfully deployed ansible-playbook.")

    def _setup_kubeconfig(self):
        self.logging.info("Setting up kubeconfig.")

        ansible_tmp_path = os.path.join(self.ansible_playbook_root, "tmp")
        kubeconfig_path = os.path.join(ansible_tmp_path, "kubeconfig.yaml")

        utils.mkdir_p("/etc/kubernetes")
        utils.mkdir_p("/etc/kubernetes/tls")
        shutil.copy(os.path.join(ansible_tmp_path, "k8s_ca.pem"),
                    "/etc/kubernetes/tls/ca.pem")
        shutil.copy(os.path.join(ansible_tmp_path, "k8s_admin.pem"),
                    "/etc/kubernetes/tls/admin.pem")
        shutil.copy(os.path.join(ansible_tmp_path, "k8s_admin-key.pem"),
                    "/etc/kubernetes/tls/admin-key.pem")
        shutil.copy(os.path.join(ansible_tmp_path, "k8s_node.pem"),
                    "/etc/kubernetes/tls/node.pem")
        shutil.copy(os.path.join(ansible_tmp_path, "k8s_node-key.pem"),
                    "/etc/kubernetes/tls/node-key.pem")

        with open(kubeconfig_path) as f:
            content = yaml.full_load(f)
        for cluster in content["clusters"]:
            cluster["cluster"]["server"] = "https://kubernetes"
        with open(kubeconfig_path, "w") as f:
            yaml.dump(content, f)
        os.environ["KUBECONFIG"] = kubeconfig_path

    def _waitForConnection(self, machines, windows):
        self.logging.info("Waiting for connection to %s.", machines)
        cmd = ["ansible"]
        cmd.append("'%s'" % " ".join(machines))

        if not windows:
            cmd.append("--key-file=%s" % self.opts.ssh_private_key_path)
        cmd.append("-m")
        cmd.append("wait_for_connection")
        cmd.append("-a")
        cmd.append("'connect_timeout=5 sleep=5 timeout=600'")

        out, _, ret = utils.run_cmd(cmd,
                                    stdout=True,
                                    cwd=self.ansible_playbook_root,
                                    shell=True)
        return ret, out.decode('ascii').strip()

    def _copyTo(self, src, dest, machines, windows=False, root=False):
        self.logging.info("Copying file %s to %s on %s.", src, dest, machines)
        cmd = ["ansible"]
        if root:
            cmd.append("--become")
        if not windows:
            cmd.append("--key-file=%s" % self.opts.ssh_private_key_path)

        cmd.append("'%s'" % " ".join(machines))
        cmd.append("-m")
        module = "win_copy" if windows else "copy"
        cmd.append(module)
        cmd.append("-a")
        cmd.append("'src=%(src)s dest=%(dest)s'" % {"src": src, "dest": dest})

        ret, out = self._waitForConnection(machines, windows=windows)
        if ret != 0:
            self.logging.error("No connection to machines. Error: %s", out)
            raise Exception("No connection to machines. Error: %s" % out)

        # Ansible logs everything to stdout
        out, _, ret = utils.run_cmd(cmd,
                                    stdout=True,
                                    cwd=self.ansible_playbook_root,
                                    shell=True)
        out = out.decode('ascii').strip()
        if ret != 0:
            self.logging.error(
                "Ansible failed to copy file %s with error: %s", src, out)
            raise Exception("Ansible failed to copy file %s with error: %s" %
                            (src, out))

    def _copyFrom(self, src, dest, machine, windows=False, root=False):
        self.logging.info("Copying file %s:%s to %s.", machine, src, dest)
        cmd = ["ansible"]
        if root:
            cmd.append("--become")
        if not windows:
            cmd.append("--key-file=%s" % self.opts.ssh_private_key_path)
        cmd.append(machine)
        cmd.append("-m")
        cmd.append("fetch")
        cmd.append("-a")
        cmd.append("'src=%(src)s dest=%(dest)s flat=yes'" % {
            "src": src,
            "dest": dest
        })

        # TODO: (atuvenie) This could really be a decorator
        ret, _ = self._waitForConnection([machine], windows=windows)
        if ret != 0:
            self.logging.error("No connection to machine: %s", machine)
            raise Exception("No connection to machine: %s" % machine)

        out, _, ret = utils.run_cmd(cmd,
                                    stdout=True,
                                    cwd=self.ansible_playbook_root,
                                    shell=True)
        out = out.decode('ascii').strip()

        if ret != 0:
            self.logging.error(
                "Ansible failed to fetch file from %s with error: %s",
                machine, out)
            raise Exception(
                "Ansible failed to fetch file from %s with error: %s" %
                (machine, out))

    # Used by _runRemoteCmd to add more retries
    def _parseAnsibleOutput(self, out):
        json_data = json.loads(out)
        machinesToRetry = []
        for vm in json_data['stats'].keys():
            if json_data['stats'][vm]['failures'] == 1 or json_data['stats'][
                    vm]['unreachable'] == 1:
                machinesToRetry.append(vm)
        return machinesToRetry

    def _runRemoteCmd(self,
                      command,
                      machines,
                      retries,
                      windows=False,
                      root=False):
        self.logging.info("Running cmd %s on remote machines %s.",
                          command, machines)

        def _runRemoteAnsible(command, machines, windows=False, root=False):
            cmd = ["ansible"]
            if root:
                cmd.append("--become")
            if windows:
                task = "win_shell"

                if root:
                    cmd.append("--become-method=runas")
                    cmd.append("--become-user=%s" %
                               constants.WINDOWS_ADMIN_USER)
            else:
                task = "shell"
                cmd.append("--key-file=%s" % self.opts.ssh_private_key_path)
            cmd.append("'%s'" % " ".join(machines))
            cmd.append("-m")
            cmd.append(task)
            cmd.append("-a")
            cmd.append("'%s'" % command)
            ret, out = self._waitForConnection(machines, windows=windows)
            if ret != 0:
                self.logging.error("No connection to machines. Error: %s", out)
            out, _, ret = utils.run_cmd(cmd,
                                        stdout=True,
                                        cwd=self.ansible_playbook_root,
                                        shell=True)
            return ret, out.decode('ascii').strip()

        ret, out = _runRemoteAnsible(command, machines, windows, root)
        while retries != 0 and ret != 0:
            self.logging.info("Ansible failed to run command %s. Retrying.",
                              command)
            retries -= 1
            ret, out = _runRemoteAnsible(
                command,
                machines=self._parseAnsibleOutput(out),
                windows=windows,
                root=root)
        if ret == 0:
            self.logging.info(
                "Ansible succesfully ran command %s on machines %s.",
                command, machines)
        else:
            self.logging.error("Ansible failed to run command %s "
                               "on machines %s with error: %s",
                               command, machines, out)
            raise Exception(
                ("Ansible failed to run command %s "
                 "on machines %s with error: %s") % (command, machines, out))

    def _applySettings(self):
        # TODO: This path should be passed as param
        settings_script = os.path.join(os.getcwd(), "settings.ps1")
        self.logging.info("Copying settings script to all windows nodes.")
        vms = self.deployer.get_cluster_win_minion_vms_names()
        self._copyTo(settings_script, "c:\\", vms, windows=True)
        self._runRemoteCmd(("c:\\settings.ps1"),
                           vms,
                           self.opts.remoteCmdRetries,
                           windows=True)

    def _prepullImages(self):
        self.logging.info("Starting image prepull.")

        daemonset_name = "prepull"
        kubectl = utils.get_kubectl_bin()
        cmd = [kubectl, "create", "-f", self.opts.prepull_yaml]
        out, _, ret = utils.run_cmd(cmd, stdout=True)
        out = out.decode('ascii').strip()

        if ret != 0:
            self.logging.error("Failed to start daemonset: %s", out)
            raise Exception("Failed to start daemonset: %s" % out)

        # Sleep for 15 minutes
        time.sleep(900)

        if not utils.daemonset_cleanup(self.opts.prepull_yaml, daemonset_name):
            self.logging.error("Timed out waiting for daemonset cleanup: %s",
                               daemonset_name)
            raise Exception("Timed out waiting for daemonset cleanup: %s" %
                            daemonset_name)

        self.logging.info("Succesfully prepulled images.")

    def _prepare_test_env(self):
        os.environ["KUBE_MASTER"] = "local"
        os.environ["KUBE_MASTER_IP"] = "kubernetes"
        os.environ["KUBE_MASTER_URL"] = "https://kubernetes"

        self._prepullImages()

    def _build_k8s_binaries(self):
        k8s_path = utils.get_k8s_folder()
        utils.clone_repo(self.opts.k8s_repo, self.opts.k8s_branch, k8s_path)
        utils.build_k8s_binaries()

    def _build_containerd_binaries(self):
        containerd_path = utils.get_containerd_folder()
        utils.clone_repo(self.opts.containerd_repo,
                         self.opts.containerd_branch, containerd_path)
        ctr_path = utils.get_ctr_folder()
        utils.clone_repo(self.opts.ctr_repo, self.opts.ctr_branch, ctr_path)
        utils.build_containerd_binaries()

    def _build_containerd_shim(self):
        if self.opts.containerd_shim_repo is None:
            fromVendor = True
        else:
            fromVendor = False

        containerd_shim_path = utils.get_containerd_shim_folder(fromVendor)

        if not fromVendor:
            utils.clone_repo(self.opts.containerd_shim_repo,
                             self.opts.containerd_shim_branch,
                             containerd_shim_path)

        utils.build_containerd_shim(containerd_shim_path, fromVendor)

    def _build_sdn_binaries(self):
        sdn_path = utils.get_sdn_folder()
        utils.clone_repo(self.opts.sdn_repo, self.opts.sdn_branch, sdn_path)
        utils.build_sdn_binaries()

    def build(self, bins_to_build):
        builder_mapping = {
            "k8sbins": self._build_k8s_binaries,
            "containerdbins": self._build_containerd_binaries,
            "containerdshim": self._build_containerd_shim,
            "sdnbins": self._build_sdn_binaries
        }

        def noop_func():
            pass

        for bins in bins_to_build:
            self.logging.info("Building %s binaries.", bins)
            builder_mapping.get(bins, noop_func)()

    def up(self):
        self.logging.info("Bringing cluster up.")
        try:
            self.deployer.up()
            self._prepare_ansible()
            self._add_ssh_key()
            if self.patches is not None:
                self._install_patches()
            self._deploy_ansible()
            self._setup_kubeconfig()
            self._applySettings()
        except Exception as e:
            raise e

    def reclaim(self):
        self.logging.info("Reclaiming cluster.")
        try:
            with open(self.opts.terraformData, 'r') as file:
                data = file.read().replace('\n', '')
            terraform_data = ast.literal_eval(data)
            self.deployer.parse_terraform_output(terraform_data)
            self.deployer.populate_hosts_file()
            master = self.deployer.get_cluster_master_vm_name()

            self._prepare_ansible()
            for file in [
                    "ca.pem", "ca-key.pem", "admin.pem", "admin-key.pem",
                    "node.pem", "node-key.pem"
            ]:
                dest = os.path.join(self.ansible_playbook_root, "tmp",
                                    "k8s_%s" % file)
                self._copyFrom("/etc/kubernetes/tls/%s" % file,
                               dest,
                               master,
                               root=True)
            self._copyFrom("/etc/kubernetes/kubeconfig.yaml",
                           os.path.join(self.ansible_playbook_root, "tmp",
                                        "kubeconfig.yaml"),
                           master,
                           root=True)
            self._setup_kubeconfig()
        except Exception as e:
            raise e

    def down(self):
        self.logging.info("Destroying cluster.")
        try:
            self.deployer.down()
        except Exception as e:
            raise e

    def _collect_logs(self, daemonset_yaml, script_url, operating_system):
        if "KUBECONFIG" not in os.environ:
            self.logging.info(("Skipping collection of %s logs, because "
                               "KUBECONFIG is not set."), operating_system)
            return

        self.logging.info("Collecting %s logs.", operating_system)
        daemonset_name = "collect-logs-%s" % operating_system

        utils.mkdir_p("/tmp/collect-logs")
        daemonset_yaml_file = "/tmp/collect-logs/collect-logs-%s.yaml" % (
            operating_system)
        utils.download_file(daemonset_yaml, daemonset_yaml_file)
        utils.sed_inplace(daemonset_yaml_file, "{{SCRIPT_URL}}", script_url)

        kubectl = utils.get_kubectl_bin()

        utils.run_shell_cmd([kubectl, "create", "-f", daemonset_yaml_file])
        out, _ = utils.run_shell_cmd([
            kubectl, "get", "pods",
            "--selector=name=%s" % daemonset_name,
            "--output=custom-columns=NAME:.metadata.name", "--no-headers"
        ])

        log_pods = out.decode('ascii').strip().splitlines()
        for pod in log_pods:
            if not utils.wait_for_ready_pod(pod):
                self.logging.warning(
                    "Timed out waiting for pod to be ready: %s", pod)
                continue

            out, _ = utils.run_shell_cmd([
                kubectl, "get", "pod", pod,
                "--output=custom-columns=NODE:.spec.nodeName", "--no-headers"
            ])
            vm_name = out.decode('ascii').strip()

            self.logging.info("Copying logs from: %s", vm_name)

            logs_vm_path = os.path.join(self.opts.log_path, "%s.zip" % vm_name)

            if operating_system == "linux":
                src_path = "%s:/tmp/k8s-logs.tar.gz" % pod
            else:
                src_path = "%s:k/logs.zip" % pod

            utils.run_shell_cmd([kubectl, "cp", src_path, logs_vm_path])

        if not utils.daemonset_cleanup(daemonset_yaml_file, daemonset_name):
            self.logging.error("Timed out waiting for daemonset cleanup: %s",
                               daemonset_name)
            raise Exception("Timed out waiting for daemonset cleanup: %s" %
                            daemonset_name)

        self.logging.info("Finished collecting %s logs.", operating_system)

    def collectWindowsLogs(self):
        self._collect_logs(self.opts.collect_logs_windows_yaml,
                           self.opts.collect_logs_windows_script, "windows")

    def collectLinuxLogs(self):
        self._collect_logs(self.opts.collect_logs_linux_yaml,
                           self.opts.collect_logs_linux_script, "linux")
