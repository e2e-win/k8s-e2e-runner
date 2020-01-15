# Create tmp directory
mkdir c:\tmp

# Disable realtime monitoring
Write-Host "Disable monitoring"
Set-MpPreference -DisableRealtimeMonitoring $true

# Resize partition to 100GB as vm image has only 30GB
Write-Host "Resizing partition"
$MaxSize = (Get-PartitionSupportedSize -DriveLetter "c").sizeMax
Resize-Partition -DriveLetter "c" -Size $MaxSize

[System.Environment]::SetEnvironmentVariable('DOCKER_API_VERSION', "1.39", [System.EnvironmentVariableTarget]::Machine)
