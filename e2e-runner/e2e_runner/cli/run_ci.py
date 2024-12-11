import os
import time
import traceback

import azure.mgmt.containerservice.models as aks_models
from cliff.command import Command
from e2e_runner import constants as e2e_constants
from e2e_runner import exceptions as e2e_exceptions
from e2e_runner import factory as e2e_factory
from e2e_runner import logger as e2e_logger
from e2e_runner.utils import utils as e2e_utils


class RunCI(Command):
    """Run the E2E Runner CI"""
    logging = e2e_logger.get_logger(__name__)

    def get_parser(self, prog_name):
        p = super(RunCI, self).get_parser(prog_name)

        p.add_argument(
            "--artifacts-directory",
            default="/tmp/ci_artifacts",
            help="Local path to place all the artifacts.")
        p.add_argument(
            "--build",
            action="append",
            default=[],
            choices=[
                "k8sbins", "containerdbins", "containerdshim", "sdncnibins",
                "critools",
            ],
            help="Binaries to build.")

        p.add_argument(
            "--parallel-test-nodes",
            default=4)
        p.add_argument(
            "--repo-list",
            default="https://capzwin.blob.core.windows.net/images/image-repo-list",  # noqa
            help="Repo list with registries for test images.")
        p.add_argument(
            "--conformance-image",
            help="Conformance test image to use for the E2E tests.")
        p.add_argument(
            "--e2e-bin",
            default=None,
            help="URL with the Kubernetes E2E tests binary.")
        p.add_argument(
            "--test-focus-regex",
            default="\\[Conformance\\]|\\[NodeConformance\\]|\\[sig-windows\\]")  # noqa
        p.add_argument(
            "--test-skip-regex",
            default="\\[LinuxOnly\\]")
        p.add_argument(
            "--test-regex-file-url",
            default=None,
            help="URL with the file containing the test regexes. This must "
                 "be a YAML file. The file must contain a 'focus' and a "
                 "'skip' key with the regular expressions for the tests. "
                 "If this is set, the parameters '--test-focus-regex' and "
                 "'--test-skip-regex' are ignored.")
        p.add_argument(
            "--retain-testing-env",
            type=e2e_utils.str2bool,
            default=False,
            help="Retain the testing environment, if the conformance tests "
                 "failed. Useful for debugging purposes.")
        p.add_argument(
            "--flake-attempts",
            type=int,
            default=0,
            help="Ginkgo flake attempts. If the value is greater than 0, the "
                 "E2E tests will be run multiple times, until they pass or "
                 "the number of attempts is reached.")

        p.add_argument(
            "--k8s-repo",
            default="https://github.com/kubernetes/kubernetes")
        p.add_argument(
            "--k8s-branch",
            default="master")

        p.add_argument(
            "--containerd-repo",
            default="https://github.com/containerd/containerd")
        p.add_argument(
            "--containerd-branch",
            default="main")

        p.add_argument(
            "--containerd-shim-repo",
            default="https://github.com/microsoft/hcsshim")
        p.add_argument(
            "--containerd-shim-branch",
            default="main")

        p.add_argument(
            "--sdn-repo",
            default="https://github.com/microsoft/windows-container-networking")  # noqa
        p.add_argument(
            "--sdn-branch",
            default="master")

        p.add_argument(
            "--cri-tools-repo",
            default="https://github.com/kubernetes-sigs/cri-tools",
            help="The cri-tools repository. It is used to build the "
                 "crictl tool.")
        p.add_argument(
            "--cri-tools-branch",
            default="master",
            help="The cri-tools branch.")

        subparsers = p.add_subparsers(dest="ci", help="The CI type.")
        self.add_capz_flannel_subparser(subparsers)
        self.add_aks_subparser(subparsers)

        return p

    def add_capz_flannel_subparser(self, subparsers):
        p = subparsers.add_parser("capz_flannel")
        p.add_argument(
            "--flannel-mode",
            default=e2e_constants.FLANNEL_MODE_OVERLAY,
            choices=[e2e_constants.FLANNEL_MODE_OVERLAY,
                     e2e_constants.FLANNEL_MODE_L2BRIDGE],
            help="Flannel mode used by the CI.")
        p.add_argument(
            "--cni-version",
            default="0.3.0"
        )
        p.add_argument(
            "--kubernetes-version",
            default=e2e_constants.DEFAULT_KUBERNETES_VERSION,
            help="The Kubernetes version to deploy. If '--build=k8sbins' is "
                 "specified, this parameter is overwriten by the version of "
                 "the newly built K8s binaries.")
        p.add_argument(
            "--container-image-tag",
            default="main",
            help="The tag used for all the container images. The existing "
                 "GitHub workflows used this tag, when building all the "
                 "images.")
        p.add_argument(
            "--container-image-registry",
            default="ghcr.io/e2e-win",
            help="The registry used for all the container images.")
        p.add_argument(
            "--enable-win-dsr",
            type=e2e_utils.str2bool,
            default=True,
            help="Enable WinDSR feature for kube-proxy on Windows.")
        p.add_argument(
            "--cluster-name",
            required=True,
            help="The cluster name given to the cluster-api manifest. This "
                 "value is used for the Azure resource group name as well.")
        p.add_argument(
            "--cluster-network-subnet",
            default="10.244.0.0/16",
            help="The cluster network subnet given to the "
                 "cluster-api manifest.")
        p.add_argument(
            "--location",
            help="The Azure location for the spawned resource.")
        p.add_argument(
            "--vnet-cidr-block",
            default="10.0.0.0/8",
            help="The vNET CIDR block.")
        p.add_argument(
            "--control-plane-subnet-cidr-block",
            default="10.0.0.0/16",
            help="The control plane subnet CIDR block.")
        p.add_argument(
            "--node-subnet-cidr-block",
            default="10.1.0.0/16",
            help="The node subnet CIDR block.")
        p.add_argument(
            "--bootstrap-vm-size",
            default="Standard_D2s_v3",
            help="Size of the bootstrap VM.")
        p.add_argument(
            "--master-vm-size",
            default="Standard_D2s_v3",
            help="Size of master virtual machine.")
        p.add_argument(
            "--win-agents-count",
            type=int,
            default=2,
            help="Number of K8s Windows agents for the deployment.")
        p.add_argument(
            "--win-os",
            default="ltsc2022",
            choices=["ltsc2019", "ltsc2022", "ltsc2025"],
            help="The operating system of the K8s Windows agents.")
        p.add_argument(
            "--win-agent-size",
            default="Standard_D4s_v3",
            help="Size of K8s Windows agents.")

    def add_aks_subparser(self, subparsers):
        p = subparsers.add_parser("aks")
        p.add_argument(
            "--cluster-name",
            required=True,
            help="The AKS cluster name.")
        p.add_argument(
            "--aks-version",
            default=e2e_constants.DEFAULT_AKS_VERSION,
            help="The AKS Kubernetes version to deploy.")
        p.add_argument(
            "--location",
            help="The Azure location for the spawned resource.")
        p.add_argument(
            "--linux-agents-count",
            type=int,
            default=1,
            help="Number of AKS Linux agents.")
        p.add_argument(
            "--linux-agents-size",
            default="Standard_D2s_v3",
            help="Size of the AKS Linux agents.")
        p.add_argument(
            "--win-agents-count",
            type=int,
            default=2,
            help="Number of AKS Windows agents.")
        p.add_argument(
            "--win-agents-size",
            default="Standard_D4s_v3",
            help="Size of K8s Windows agents.")
        p.add_argument(
            "--win-agents-sku",
            default=aks_models.OSSKU.WINDOWS2019,
            choices=[aks_models.OSSKU.WINDOWS2019,
                     aks_models.OSSKU.WINDOWS2022],
            help="The OS SKU of the K8s Windows agents.")

    def take_action(self, args):
        self.logging.info("Starting with CI: %s.", args.ci)
        self.logging.info(
            "Creating artifacts dir: %s.", args.artifacts_directory)
        os.makedirs(args.artifacts_directory, exist_ok=True)
        # add suffix to the cluster name to avoid resource group name
        # conflicts.
        args.cluster_name += f"-{int(time.time())}"
        ci = e2e_factory.get_ci(args.ci)(args)
        conformance_tests_failed = False
        try:
            ci.setup_bootstrap_vm()
            ci.build(args.build)
            ci.up()
            ci.cleanup_bootstrap_vm()
            ci.test()
        except Exception as ex:
            self.logging.error("{}".format(traceback.format_exc()))
            if isinstance(ex, e2e_exceptions.ConformanceTestsFailed):
                conformance_tests_failed = True
            raise
        finally:
            ci.collect_logs()
            if conformance_tests_failed and args.retain_testing_env:
                self.logging.warning(
                    "Conformance tests failed. Retain the testing env "
                    "for debugging purposes.")
            else:
                ci.down()
