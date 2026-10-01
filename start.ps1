# Windows PowerShell 5.1 会把外部程序写入 stderr 的普通信息包装成错误记录。
# 使用 Continue，并通过 $LASTEXITCODE 判断 Python/pip 是否真正失败，避免误报启动失败。
$ErrorActionPreference = "Continue"

Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    # 旧版终端不支持时继续运行，不影响核心功能。
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$installLog = Join-Path $PSScriptRoot ".bootstrap-install.log"
$pythonExe = $null
$pythonPrefix = @()

function Wait-BeforeExit {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Read-Host "按回车键关闭窗口" | Out-Null
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $script:pythonExe = "py"
            $script:pythonPrefix = @("-3.11")
            return
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $script:pythonExe = "python"
            $script:pythonPrefix = @()
        }
    }
}

try {
    $needsInstall = $false
    $venvReady = $false
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython --version *> $null
        $venvReady = $LASTEXITCODE -eq 0
    }

    if (-not $venvReady) {
        Find-Python
        if (-not $pythonExe) {
            Wait-BeforeExit "未检测到 Python 3.11 或更高版本。请按照《小白教程.md》安装 Python，并勾选 Add Python to PATH。"
            exit 1
        }

        if (Test-Path -LiteralPath $venvPython) {
            Write-Host "[启动准备] 检测到项目运行环境异常，正在自动修复……" -ForegroundColor Cyan
            & $pythonExe @pythonPrefix -m venv --clear .venv
            if ($LASTEXITCODE -ne 0) { throw "创建 Python 运行环境失败。" }
        } else {
            Write-Host "[首次启动] 正在创建本项目专用的 Python 运行环境，不要关闭窗口……" -ForegroundColor Cyan
            & $pythonExe @pythonPrefix -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw "创建 Python 运行环境失败。" }
        }
        $needsInstall = $true
    }

    if (-not $needsInstall) {
        & $venvPython -c "import cloudriver_manager, playwright" *> $null
        $needsInstall = $LASTEXITCODE -ne 0
    }

    if ($needsInstall) {
        Write-Host "[首次启动] 正在安装程序依赖和登录组件……" -ForegroundColor Cyan
        Write-Host "           首次安装通常需要几分钟，速度取决于网络，请耐心等待。"
        Write-Host "           详细安装日志见 .bootstrap-install.log。"

        & $venvPython -m pip install --disable-pip-version-check --progress-bar off --quiet -e ".[browser]" *> $installLog
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "安装没有完成，下面是安装日志的最后几行：" -ForegroundColor Yellow
            Get-Content -LiteralPath $installLog -Tail 20
            throw "安装依赖失败。请检查网络后重新运行 start.cmd。"
        }

        Write-Host "[准备完成] 运行环境安装成功，正在进入程序菜单……" -ForegroundColor Green
        Write-Host ""
    }

    & $venvPython -m cloudriver_manager
    if ($LASTEXITCODE -ne 0) {
        Wait-BeforeExit "程序因错误停止。请查看上方提示，或参考《小白教程.md》的常见问题章节。"
        exit $LASTEXITCODE
    }
} catch {
    Wait-BeforeExit "启动失败：$($_.Exception.Message)"
    exit 1
}
