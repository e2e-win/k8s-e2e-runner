import configargparse
import ci_factory
import log
import utils
import sys

p = configargparse.get_argument_parser()

logging = log.getLogger("civ2")


def parse_args():

    def str2bool(v):
        if v.lower() == "true":
            return True
        elif v.lower() == "false":
            return False
        else:
            raise configargparse.ArgumentTypeError('Boolean value expected')

    p.add('-c', '--configfile', is_config_file=True, help='Config file path.')
    p.add('--up', type=str2bool, default=False, help='Deploy test cluster.')
    p.add('--down', type=str2bool, default=False, help='Destroy cluster on finish.')
    p.add('--build', action="append", help='Build k8s binaries. Values: k8sbins, containerdbins, containerdshim, sdnbins')
    p.add('--test', type=str2bool, default=False, help='Run tests.')
    p.add('--admin-openrc', default=False, help='Openrc file for OpenStack cluster')
    p.add('--log-path', default="/tmp/civ2_logs", help='Path to place all artifacts')
    p.add('--ci', help="OVN-OVS, Flannel")
    p.add('--install-patch', action="append", help="URLs of KBs to install on Windows nodes")
    p.add('--install-lanfix', type=str2bool, default=False)
    p.add('--cluster-name', help="Name of cluster.")
    p.add('--k8s-repo', default="http://github.com/kubernetes/kubernetes")
    p.add('--k8s-branch', default="master")
    p.add('--containerd-repo', default="http://github.com/jterry75/cri")
    p.add('--containerd-branch', default="windows_port")
    p.add('--containerd-shim-repo', default=None)
    p.add('--containerd-shim-branch', default="master")
    p.add('--ctr-repo', default="https://github.com/containerd/containerd")
    p.add('--ctr-branch', default="master")
    p.add('--cnp-repo', default="https://github.com/containernetworking/plugins")
    p.add('--cnp-branch', default="master")
    p.add('--wcn-repo', default="http://github.com/microsoft/windows-container-networking")
    p.add('--wcn-branch', default="master")
    p.add('--collect-logs-windows-yaml', default="https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/master/v2/collect-logs-windows.yaml")
    p.add('--collect-logs-windows-script', default="https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/master/v2/collect-logs.ps1")
    p.add('--collect-logs-linux-yaml', default="https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/master/v2/collect-logs-linux.yaml")
    p.add('--collect-logs-linux-script', default="https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/master/v2/collect-logs.sh")
    p.add('--prepull-yaml', default="https://raw.githubusercontent.com/kubernetes-sigs/windows-testing/master/gce/prepull.yaml")
    p.add('--hold', type=str2bool, default=False, help='Useful for debugging while running in containerd. \
                                                        Sleeps the process after setting the env for testing so user can manually exec from container.')

    opts = p.parse_known_args()

    return opts


def main():
    try:
        opts = parse_args()[0]
        logging.info("Starting with CI: %s" % opts.ci)
        logging.info("Creating log dir: %s." % opts.log_path)
        utils.mkdir_p(opts.log_path)
        ci = ci_factory.get_ci(opts.ci)
        success = 0

        if opts.build is not None and \
                "containerdshim" in opts.build and \
                opts.containerd_shim_repo is None and \
                "containerdbins" not in opts.build:
            logging.error("Building containerdshim from vendoring repo without building containerd is not supported")
            sys.exit(1)

        if opts.build is not None:
            ci.build(opts.build)

        if opts.install_patch is not None:
            ci.set_patches(" ".join(opts.install_patch))

        if opts.up is True:
            if opts.down is True:
                ci.down()
            ci.up()
        if opts.install_lanfix is True:
            ci.install_lanfix()
        if opts.test is True:
            success = ci.test()
        if success != 0:
            raise Exception
        sys.exit(0)
    except Exception as e:
        logging.error(e)
        sys.exit(1)
    finally:
        ci.collectWindowsLogs()
        ci.collectLinuxLogs()
        if opts.down is True:
            ci.down()


if __name__ == "__main__":
    main()
