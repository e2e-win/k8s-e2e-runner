Param(
    [parameter(Mandatory=$true)]
    [string]$KubernetesVersion
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\common.ps1"

$CONTAINERD_DIR = Join-Path $env:SystemDrive "containerd"


function Install-Containerd {
    mkdir -force $CONTAINERD_DIR
    mkdir -force $KUBERNETES_DIR
    mkdir -force $VAR_LOG_DIR
    mkdir -force "$ETC_DIR\cni\net.d"

    $criContainerdVersion = "1.4.1"
    Start-FileDownload "https://github.com/containerd/containerd/releases/download/v${criContainerdVersion}/cri-containerd-cni-${criContainerdVersion}-windows-amd64.tar.gz" "$env:TEMP\cri-containerd-windows-amd64.tar.gz"
    tar xzf $env:TEMP\cri-containerd-windows-amd64.tar.gz -C $CONTAINERD_DIR
    if($LASTEXITCODE) {
        Throw "Failed to unzip containerd.zip"
    }
    Add-ToSystemPath $CONTAINERD_DIR
    Remove-Item -Force "$env:TEMP\cri-containerd-windows-amd64.tar.gz"

    $crictlVersion = "1.19.0"
    Start-FileDownload "https://github.com/kubernetes-sigs/cri-tools/releases/download/v${crictlVersion}/crictl-v${crictlVersion}-windows-amd64.tar.gz" "$env:TEMP\crictl-windows-amd64.tar.gz"
    tar xzf $env:TEMP\crictl-windows-amd64.tar.gz -C $KUBERNETES_DIR
    if($LASTEXITCODE) {
        Throw "Failed to unzip crictl.zip"
    }
    Remove-Item -Force "$env:TEMP\crictl-windows-amd64.tar.gz"

    Copy-Item "$PSScriptRoot\nat.conf" "$ETC_DIR\cni\net.d\"
    $k8sPauseImage = Get-KubernetesPauseImage
    Get-Content "$PSScriptRoot\containerd_config.toml" | `
        ForEach-Object { $_ -replace "{{K8S_PAUSE_IMAGE}}", $k8sPauseImage } | `
        Out-File "$CONTAINERD_DIR\containerd_config.toml" -Encoding ascii

    nssm install containerd $CONTAINERD_DIR\containerd.exe --config $CONTAINERD_DIR\containerd_config.toml
    if($LASTEXITCODE) {
        Throw "Failed to install containerd service"
    }
    nssm set containerd AppStdout $VAR_LOG_DIR\containerd.log
    if($LASTEXITCODE) {
        Throw "Failed to set AppStdout for containerd service"
    }
    nssm set containerd AppStderr $VAR_LOG_DIR\containerd.log
    if($LASTEXITCODE) {
        Throw "Failed to set AppStderr for containerd service"
    }

    nssm set containerd Start SERVICE_DEMAND_START
    if($LASTEXITCODE) {
        Throw "Failed to set containerd manual start type"
    }

    $env:CONTAINER_RUNTIME_ENDPOINT = "npipe:\\.\pipe\containerd-containerd"
    [Environment]::SetEnvironmentVariable("CONTAINER_RUNTIME_ENDPOINT", $env:CONTAINER_RUNTIME_ENDPOINT, [System.EnvironmentVariableTarget]::Machine)

    Start-Service -Name "containerd"
}

function Start-ContainerImagesPull {
    $windowsRelease = Get-WindowsRelease
    $images = @(
        (Get-KubernetesPauseImage),
        (Get-NanoServerImage),
        "mcr.microsoft.com/windows/servercore:${windowsRelease}",
        "docker.io/e2eteam/flannel-windows:v0.13.0-windowsservercore-${windowsRelease}",
        "docker.io/e2eteam/kube-proxy-windows:${KubernetesVersion}-windowsservercore-${windowsRelease}"
    )
    foreach($img in $images) {
        Start-ExecuteWithRetry {
            ctr.exe -n k8s.io image pull $img
            if($LASTEXITCODE) {
                Throw "Failed to pull image: $img"
            }
        }
    }
}


Install-NSSM
Install-CNI
Install-Containerd
Install-Kubelet -KubernetesVersion $KubernetesVersion `
                -StartKubeletScriptPath "$PSScriptRoot\StartKubelet.ps1" `
                -ContainerRuntimeServiceName "containerd"
Start-ContainerImagesPull

nssm stop containerd
if($LASTEXITCODE) {
    Throw "Failed to stop containerd"
}
$hnsNetworks = Get-HnsNetwork
if($hnsNetworks) {
    $hnsNetworks | Remove-HnsNetwork
}
