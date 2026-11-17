param([int]$Port = 8010, [string]$Camera = 'http://192.168.137.190:8000')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$trainingPython = Join-Path $projectRoot '.venv-training/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) {
    & (Join-Path $PSScriptRoot 'setup-training.ps1')
}
& $trainingPython -m pi_vision.studio.training_studio --port $Port --camera $Camera
