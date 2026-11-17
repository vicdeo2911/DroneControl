param([switch]$CpuOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$trainingPython = Join-Path $PSScriptRoot '.venv-training/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) {
    python -m venv .venv-training
    if ($LASTEXITCODE) { throw 'Could not create training environment.' }
}
& $trainingPython -m pip install --upgrade pip
if ($LASTEXITCODE) { throw 'Could not update pip.' }
if (-not $CpuOnly -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    & $trainingPython -m pip install 'torch==2.14.1+cu130' 'torchvision==0.29.1+cu130' --index-url https://download.pytorch.org/whl/cu130
    if ($LASTEXITCODE) { throw 'CUDA installation failed. See the PyTorch Windows installation selector or use -CpuOnly.' }
}
& $trainingPython -m pip install -r requirements-training.txt
if ($LASTEXITCODE) { throw 'Could not install training dependencies.' }
& $trainingPython -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
if ($LASTEXITCODE) { throw 'PyTorch verification failed.' }
