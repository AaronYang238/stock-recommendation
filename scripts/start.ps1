# 一键启动（Windows / PowerShell）：装依赖 → 建配置 → 灌数据 → 同起后端(:8000)+前端(:9090)。
# 用法：  pwsh scripts/start.ps1            # 首次会建 .venv 并安装依赖
#         pwsh scripts/start.ps1 -NoSeed    # 跳过合成数据灌库
# Ctrl+C 停止前端，脚本会一并关闭后端。
param([switch]$NoSeed)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1) 虚拟环境 + 依赖
if (-not (Test-Path .venv)) { Write-Host "[1/5] 创建虚拟环境 .venv ..."; python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
Write-Host "[2/5] 安装/校验依赖（首次较慢）..."
pip install -q -r requirements.txt
pip install -q -r backend/requirements.txt
pip install -q -e .

# 2) 配置
if (-not (Test-Path config/config.yaml)) {
  Copy-Item config/config.example.yaml config/config.yaml
  Write-Host "      已生成 config/config.yaml"
}

# 3) 数据
if (-not $NoSeed -and -not (Test-Path data_store/aselect.sqlite)) {
  Write-Host "[3/5] 灌入离线合成数据（aselect.cli seed）..."
  python -m aselect.cli seed
} else { Write-Host "[3/5] 已有数据库或 -NoSeed，跳过 seed" }

# 4) 后端
Write-Host "[4/5] 启动后端 Django :8000 ..."
python backend/manage.py migrate --noinput | Out-Null
$backend = Start-Process python -ArgumentList "backend/manage.py","runserver","127.0.0.1:8000" `
  -PassThru -WindowStyle Hidden

# 5) 前端
Set-Location frontend
if (-not (Test-Path node_modules)) { Write-Host "      首次安装前端依赖（npm install）..."; npm install }
Write-Host ""
Write-Host "============================================"
Write-Host "  前端:     http://localhost:9090"
Write-Host "  后端 API: http://127.0.0.1:8000/api/meta/"
Write-Host "  Ctrl+C 停止全部"
Write-Host "============================================"
try { npm run dev } finally {
  if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue }
}
