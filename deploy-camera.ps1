param([string]$PiAddress = 'user@192.168.137.190')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$piParts = $PiAddress.Split('@')
if ($piParts.Count -ne 2) { throw 'Use user@host for PiAddress.' }
# Reuses the credential saved by pi_ssh.py in Windows Credential Manager.
python pi_ssh.py --host $piParts[1] --user $piParts[0] --deploy-camera
if ($LASTEXITCODE) { throw 'Camera deployment/startup failed. Check camera connection and journalctl -u pi-vision -n 30.' }
Write-Host 'Camera update installed. Verify http://192.168.137.190:8000/raw.jpg before live capture.'
