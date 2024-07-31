package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"kube-proxy-windows/kube_proxy"
	"kube-proxy-windows/utils"

	k8s_client_v1 "k8s.io/client-go/tools/clientcmd/api/v1"

	"github.com/Microsoft/hcsshim/hcn"
	"k8s.io/kube-proxy/config/v1alpha1"
	"sigs.k8s.io/yaml"
)

func parseSourceVipFile() (string, error) {
	bytes, err := os.ReadFile(kube_proxy.SourceVipFile)
	if err != nil {
		return "", err
	}
	sourceVip := kube_proxy.SourceVip{}
	if err := json.Unmarshal(bytes, &sourceVip); err != nil {
		return "", err
	}
	return strings.Split(sourceVip.IPS[0].Address, "/")[0], nil
}

func getSourceVip() (string, error) {
	if _, err := os.Stat(kube_proxy.SourceVipFile); err == nil {
		sourceVip, err := parseSourceVipFile()
		if err != nil {
			return "", err
		}
		return sourceVip, nil
	}
	ipamPluginBin := filepath.Join(kube_proxy.CNIBinDir, "host-local.exe")
	if _, err := os.Stat(ipamPluginBin); os.IsNotExist(err) {
		return "", fmt.Errorf("ipam plugin binary (%s) does not exist", ipamPluginBin)
	}
	network, err := utils.GetHnsNetwork()
	if err != nil {
		return "", err
	}
	subnet := network.Ipams[0].Subnets[0].IpAddressPrefix
	cniVersion := os.Getenv("CNI_VERSION")
	ipamConfig := fmt.Sprintf(`
	{
		"cniVersion": "%s",
		"name": "%s",
		"ipam": {
			"type": "host-local",
			"ranges": [
				[{"subnet": "%s"}]
			],
			"dataDir": "/var/lib/cni/networks"
		}
	}`, cniVersion, network.Name, subnet)
	os.Setenv("CNI_COMMAND", "ADD")
	os.Setenv("CNI_CONTAINERID", "SourceVip")
	os.Setenv("CNI_NETNS", "SourceVip")
	os.Setenv("CNI_IFNAME", "SourceVip")
	os.Setenv("CNI_PATH", kube_proxy.CNIBinDir)
	cmd := exec.Command(ipamPluginBin)
	cmd.Stdin = strings.NewReader(ipamConfig)
	log.Println("allocating kube-proxy source VIP")
	out, err := cmd.Output()
	if err != nil {
		log.Printf("Command output: %s", out)
		return "", fmt.Errorf("error running host-local.exe ipam plugin: %v", err)
	}
	sourceVipDir := filepath.Dir(kube_proxy.SourceVipFile)
	if err := os.MkdirAll(sourceVipDir, os.ModeDir); err != nil {
		return "", fmt.Errorf("failed to create kube-proxy source vip directory %s: %v", sourceVipDir, err)
	}
	if err := os.WriteFile(kube_proxy.SourceVipFile, out, 0644); err != nil {
		return "", err
	}
	sourceVip, err := parseSourceVipFile()
	if err != nil {
		return "", err
	}
	return sourceVip, nil
}

func fixKubeProxyKubeconfig(kubeconfigFile string) (string, error) {
	kubeConfig, err := os.ReadFile(kubeconfigFile)
	if err != nil {
		return "", err
	}
	config := k8s_client_v1.Config{}
	if err := yaml.Unmarshal(kubeConfig, &config); err != nil {
		return "", err
	}
	for i := range config.Clusters {
		config.Clusters[i].Cluster.CertificateAuthority = filepath.Join(os.Getenv("SystemDrive"), config.Clusters[i].Cluster.CertificateAuthority)
	}
	for i := range config.AuthInfos {
		config.AuthInfos[i].AuthInfo.TokenFile = filepath.Join(os.Getenv("SystemDrive"), config.AuthInfos[i].AuthInfo.TokenFile)
	}
	bytes, err := yaml.Marshal(config)
	if err != nil {
		return "", err
	}
	kubeconfigDir := filepath.Dir(kube_proxy.WindowsKubeconfigFile)
	if err := os.MkdirAll(kubeconfigDir, os.ModeDir); err != nil {
		return "", fmt.Errorf("failed to create kube-proxy kubeconfig directory %s: %v", kubeconfigDir, err)
	}
	if err := os.WriteFile(kube_proxy.WindowsKubeconfigFile, bytes, 0644); err != nil {
		return "", err
	}
	return kube_proxy.WindowsKubeconfigFile, nil
}

func createKubeProxyConfig() error {
	log.Printf("creating kube-proxy config %s", kube_proxy.WindowsConfFile)

	bytes, err := os.ReadFile(kube_proxy.ConfFile)
	if err != nil {
		return err
	}
	cfg := v1alpha1.KubeProxyConfiguration{}
	if err := yaml.Unmarshal(bytes, &cfg); err != nil {
		return err
	}

	network, err := utils.GetHnsNetwork()
	if err != nil {
		return err
	}
	enableDSR, err := strconv.ParseBool(os.Getenv("ENABLE_WIN_DSR"))
	if err != nil {
		return err
	}

	cfg.Mode = "kernelspace"
	cfg.Winkernel.NetworkName = network.Name
	cfg.Winkernel.EnableDSR = enableDSR

	cfg.FeatureGates = make(map[string]bool)
	cfg.FeatureGates["WinDSR"] = enableDSR
	if network.Type == hcn.Overlay {
		sourceVip, err := getSourceVip()
		if err != nil {
			return err
		}
		cfg.Winkernel.SourceVip = sourceVip
		cfg.FeatureGates["WinOverlay"] = true
	}

	// NOTE: Certificate authority and token file paths in kubeconfig are not
	// valid on Windows. The file paths are relative to the location where
	// kubeconfig is on the disk, instead of the "SystemDrive" (usually "C:").
	kubeconfigFile, err := fixKubeProxyKubeconfig(cfg.ClientConnection.Kubeconfig)
	if err != nil {
		return err
	}
	cfg.ClientConnection.Kubeconfig = kubeconfigFile

	yamlBytes, err := yaml.Marshal(cfg)
	if err != nil {
		return err
	}
	configDir := filepath.Dir(kube_proxy.WindowsConfFile)
	if err := os.MkdirAll(configDir, os.ModeDir); err != nil {
		return fmt.Errorf("failed to create kube-proxy config directory %s: %v", configDir, err)
	}
	if err := os.WriteFile(kube_proxy.WindowsConfFile, yamlBytes, 0644); err != nil {
		return err
	}

	return nil
}

func main() {
	if err := createKubeProxyConfig(); err != nil {
		panic(fmt.Errorf("error creating kube-proxy config: %v", err))
	}
}
