param([int]$Port = 8010, [string]$Camera = 'http://192.168.137.190:8000')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$trainingPython = Join-Path $PSScriptRoot '.venv-training/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) {
    & (Join-Path $PSScriptRoot 'setup-training.ps1')
}
& $trainingPython training_studio.py --port $Port --camera $Camera
