#Requires -Version 5
<#
一键执行 TUNING_GUIDE Step 2~5（PowerShell 版，test_gpu）
对应 Python 版: scripts/auto_run_steps2to5.py
新用户: 800080270733_4206673297219（可改 $UserKey）

Step2 训练 → Step3 窗口自检 → Step5 推理 → Step4 阈值扫描（test/infer 双链）
* 通配由 threshold_sweep.py 内部 glob 展开，已修复 PowerShell OSError (v2026-09-19)

用法（仓库根目录）：
  conda activate test_gpu
  .\scripts\one_click_733.ps1                          # 默认 733 p1/on10/dec30
  .\scripts\one_click_733.ps1 -UserKey 800080270856_4206810972139 -TargetCol p2
  .\scripts\one_click_733.ps1 -Stage threshold          # 仅扫阈值
#>
param(
  [string]$UserKey = "800080270733_4206673297219",
  [string]$TargetCol = "p1",
  [double]$OnThr = 10.0,
  [double]$DecisionThr = 30.0,
  [ValidateSet("all","train","infer","threshold")][string]$Stage = "all",
  [string]$TimeFilterConfig = "configs/time_filters.json",
  [string]$BaseConfig = "configs/base_optimal.yaml",
  [string]$DataRoot = "data",
  [string]$OutputRoot = "outputs",
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Step($cmd) {
  Write-Host "[RUN] $cmd" -ForegroundColor Cyan
  Invoke-Expression $cmd
  if ($LASTEXITCODE -ne 0) { throw "命令失败 exit=$LASTEXITCODE : $cmd" }
}

Write-Host "=== TUNING_GUIDE Step 2-5 一键 (User=$UserKey Stage=$Stage) ===" -ForegroundColor Green

# --force 透传
$forceFlag = if ($Force) { " --force" } else { "" }

if ($Stage -in @("all","train")) {
  Write-Host "`n--- Step2 训练 ---" -ForegroundColor Yellow
  Invoke-Step "python scripts/run_batch_users.py --time-filter-config $TimeFilterConfig --base-config $BaseConfig --data-root $DataRoot --output-root $OutputRoot --user-key $UserKey$forceFlag"
}

if ($Stage -in @("all","train")) {
  Write-Host "`n--- Step3 窗口连续性自检 (cross_gap 0) ---" -ForegroundColor Yellow
  # 与 TUNING_GUIDE 一致的 Python 单行（* 由 Python glob 展开，不依赖 PowerShell）
  $code = "import pandas,glob;f=sorted(glob.glob('outputs/$UserKey/train/*/train_window_index.csv'))[-1];w=pandas.read_csv(f);s=pandas.to_datetime(w.win_end)-pandas.to_datetime(w.win_start);print('windows',len(w),'cross_gap',(s>pandas.Timedelta('23h45m')).sum(),'max',s.max())"
  Invoke-Step "python -c `"$code`""
}

if ($Stage -in @("all","infer")) {
  Write-Host "`n--- Step5 推理 ---" -ForegroundColor Yellow
  Invoke-Step "python scripts/run_batch_users.py --time-filter-config $TimeFilterConfig --base-config $BaseConfig --data-root $DataRoot --output-root $OutputRoot --user-key $UserKey --stage infer$forceFlag"
}

if ($Stage -in @("all","threshold")) {
  Write-Host "`n--- Step4 阈值扫描 test链 ---" -ForegroundColor Yellow
  # * 用引号包起来交由 Python glob 展开，已修复 OSError
  Invoke-Step "python scripts/threshold_sweep.py --csv `"outputs/$UserKey/train/*/predictions/train_predictions.csv`" --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3"
  Write-Host "`n--- Step4 阈值扫描 infer链 ---" -ForegroundColor Yellow
  Invoke-Step "python scripts/threshold_sweep.py --csv `"outputs/$UserKey/infer/*/predictions/inference_result.csv`" --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500"
}

Write-Host "`n=== 完成 outputs/$UserKey/{train,infer}/<ts>/  后续 Step6 审计见 TUNING_GUIDE §3 ===" -ForegroundColor Green
