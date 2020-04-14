param (
    [parameter(Mandatory=$true)]
    [String] $ArchivePath
)

function Get-WindowsErrors {
    param(
        [String] $Destination
    )

    $path = Join-Path -Path $Destination -ChildPath "windows-errors"
    New-Item -ItemType directory -Path $path | Out-Null

    $reboots = Get-WinEvent -FilterHashtable @{logname='System'; id=1074,1076,2004,6005,6006,6008} `
                   | Select-Object -Property TimeCreated, Id, LevelDisplayName, Message
    $crashes = Get-WinEvent -FilterHashtable @{logname='Application'; ProviderName='Windows Error Reporting'} -ErrorAction SilentlyContinue `
                   | Select-Object -Property TimeCreated, Id, LevelDisplayName, Message

    $rebootsPath = Join-Path -Path $path -ChildPath "reboots.txt"
    $crashesPath = Join-Path -Path $path -ChildPath "crashes.txt"

    $reboots | Out-File -FilePath $rebootsPath
    $crashes | Out-File -FilePath $crashesPath
}

function Get-DockerLogs {
    param(
        [String] $Destination
    )

    $path = Join-Path -Path $Destination -ChildPath "docker-logs"
    New-Item -ItemType directory -Path $path | Out-Null

    if (Test-Path "C:\Program Files\Docker\dockerd.log*") {
        Copy-Item -Path "C:\Program Files\Docker\dockerd.log*" -Destination $path
    }

    if (Test-Path "C:\Program Files\Docker\dockerd-servicewrapper-config.ini") {
        Copy-Item -Path "C:\Program Files\Docker\dockerd-servicewrapper-config.ini" -Destination $path
    }
}

# Creates a WindowsLogs folder at the required destination
function Get-WindowsLogs {
    param(
        [String] $Destination
    )

    $path = Join-Path -Path $Destination -ChildPath "windows-logs"
    New-Item -ItemType directory -Path $path | Out-Null

    Get-WindowsErrors -Destination $path
    Get-DockerLogs -Destination $path
}

function Get-ServiceLogs {
    param(
        [String[]] $Services,
        [String] $Destination
    )

    foreach ($service in $Services) {
        $path = Join-Path -Path $Destination -ChildPath "$service-logs"
        New-Item -ItemType directory -Path $path | Out-Null

        if (Test-Path "C:\k\$service-servicewrapper-config.ini") {
            Copy-Item -Path "C:\k\$service-servicewrapper-config.ini" -Destination $path
        }

        if (Test-Path "C:\k\$service*.log") {
            Copy-Item -Path "C:\k\$service*.log" -Destination $path
        }
    }
}


# Creates a KubernetesLogs folder at the required destination
function Get-KubernetesLogs {
    param(
        [String] $Destination
    )

    $flannelServices = @("kubelet", "kube-proxy", "flanneld")
    $containerdServices = @("containerd")

    $path = Join-Path -Path $Destination -ChildPath "kubernetes-logs"
    New-Item -ItemType directory -Path $path | Out-Null

    Get-ServiceLogs -Services $flannelServices -Destination $path
    Get-Servicelogs -Services $containerdServices -Destination $path
}

function Main {

    $Destination = Split-Path -Path $ArchivePath
    $path = Join-Path -Path $Destination -ChildPath "logs"
    New-Item -ItemType directory -Path $path | Out-Null

    Get-WindowsLogs -Destination $path
    Get-KubernetesLogs -Destination  $path
    
    Compress-Archive -Path $path -CompressionLevel Optimal -DestinationPath $ArchivePath -Force

    Remove-Item -Recurse -Force -Path $path
}

Main
Write-Host "Archive created."
New-Item -ItemType file C:\k\collect-logs.done -Force
Start-Sleep -Seconds 3600
