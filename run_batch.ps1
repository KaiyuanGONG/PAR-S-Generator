# ============================================================
#  SIMIND 批量并行生产脚本
#  用法：在 PowerShell 中运行
#    cd "D:\PFE-U\PAR-S-Generator"
#    powershell -ExecutionPolicy Bypass -File scripts\run_batch.ps1
# ============================================================

# --- 配置 ---
$MAX_PARALLEL = 16          # 最大并行进程数（24物理核建议16）
$NN = 5                     # 光子乘数
$SMC = "simind\ge870_czt"   # smc文件（不含.smc后缀）
$INPUT_DIR = "output\trans"  # bin文件所在目录
$OUTPUT_DIR = "output\SPECT" # 输出目录
$CASE_START = 1
$CASE_END = 500
$LOG_FILE = "output\SPECT\batch_log.txt"

# --- 初始化 ---
Set-Location "D:\PFE-U\PAR-S-Generator"
New-Item -ItemType Directory -Force -Path $OUTPUT_DIR | Out-Null

$total = $CASE_END - $CASE_START + 1
$completed = 0
$failed = @()
$startTime = Get-Date

"Batch started: $startTime" | Out-File $LOG_FILE
"Config: MAX_PARALLEL=$MAX_PARALLEL, NN=$NN, Cases=$CASE_START-$CASE_END" | Add-Content $LOG_FILE

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  SIMIND Batch Production" -ForegroundColor Cyan
Write-Host "  Cases: $CASE_START to $CASE_END ($total total)" -ForegroundColor Cyan
Write-Host "  Parallel: $MAX_PARALLEL processes" -ForegroundColor Cyan
Write-Host "  NN: $NN" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- 主循环 ---
$queue = @($CASE_START..$CASE_END)
$running = @{}  # case_id -> Process object

foreach ($i in $queue) {
    $case_id = "case_{0:D4}" -f $i

    # 检查输入文件是否存在
    $act_file = "$INPUT_DIR\${case_id}_act_av.bin"
    $atn_file = "$INPUT_DIR\${case_id}_atn_av.bin"
    
    if (-not (Test-Path $act_file)) {
        Write-Host "  SKIP: $act_file not found" -ForegroundColor Yellow
        "$case_id SKIPPED: act file missing" | Add-Content $LOG_FILE
        $completed++
        continue
    }
    if (-not (Test-Path $atn_file)) {
        Write-Host "  SKIP: $atn_file not found" -ForegroundColor Yellow
        "$case_id SKIPPED: atn file missing" | Add-Content $LOG_FILE
        $completed++
        continue
    }

    # 检查是否已经生成过（跳过已完成的）
    if (Test-Path "$OUTPUT_DIR\${case_id}.a00") {
        Write-Host "  SKIP: $case_id already done" -ForegroundColor DarkGray
        $completed++
        continue
    }

    # 等待空闲槽位
    while ($running.Count -ge $MAX_PARALLEL) {
        Start-Sleep -Milliseconds 500
        
        # 检查已完成的进程
        $done = @()
        foreach ($key in $running.Keys) {
            if ($running[$key].HasExited) {
                $done += $key
            }
        }
        
        foreach ($key in $done) {
            $running.Remove($key)
            $completed++
            
            # 检查是否成功
            if (Test-Path "$OUTPUT_DIR\${key}.a00") {
                $elapsed = (Get-Date) - $startTime
                $rate = $completed / $elapsed.TotalMinutes
                $eta = ($total - $completed) / $rate
                Write-Host "  OK: $key [$completed/$total] ETA: $([math]::Round($eta,0))min" -ForegroundColor Green
                "$key OK" | Add-Content $LOG_FILE
            } else {
                Write-Host "  FAIL: $key" -ForegroundColor Red
                "$key FAILED" | Add-Content $LOG_FILE
                $failed += $key
            }
        }
    }

    # 启动新进程
    $args = "$SMC $OUTPUT_DIR\$case_id /FD:$INPUT_DIR\$case_id /FS:$INPUT_DIR\$case_id /NN:$NN"
    $proc = Start-Process simind -ArgumentList $args -NoNewWindow -PassThru
    $running[$case_id] = $proc
    Write-Host "  START: $case_id (PID: $($proc.Id))" -ForegroundColor DarkCyan
}

# 等待最后一批完成
Write-Host "`nWaiting for final batch..." -ForegroundColor Cyan
while ($running.Count -gt 0) {
    Start-Sleep -Milliseconds 500
    $done = @()
    foreach ($key in $running.Keys) {
        if ($running[$key].HasExited) {
            $done += $key
        }
    }
    foreach ($key in $done) {
        $running.Remove($key)
        $completed++
        if (Test-Path "$OUTPUT_DIR\${key}.a00") {
            Write-Host "  OK: $key [$completed/$total]" -ForegroundColor Green
            "$key OK" | Add-Content $LOG_FILE
        } else {
            Write-Host "  FAIL: $key" -ForegroundColor Red
            "$key FAILED" | Add-Content $LOG_FILE
            $failed += $key
        }
    }
}

# --- 汇总 ---
$endTime = Get-Date
$totalTime = $endTime - $startTime

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  DONE!" -ForegroundColor Green
Write-Host "  Total time: $($totalTime.Hours)h $($totalTime.Minutes)m $($totalTime.Seconds)s" -ForegroundColor Cyan
Write-Host "  Completed: $completed / $total" -ForegroundColor Cyan
Write-Host "  Failed: $($failed.Count)" -ForegroundColor $(if ($failed.Count -gt 0) { "Red" } else { "Green" })

if ($failed.Count -gt 0) {
    Write-Host "  Failed cases:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
}

# 统计输出文件
$a00_count = (Get-ChildItem "$OUTPUT_DIR\*.a00" -ErrorAction SilentlyContinue).Count
Write-Host "  Output .a00 files: $a00_count" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

"Batch finished: $endTime" | Add-Content $LOG_FILE
"Total time: $totalTime" | Add-Content $LOG_FILE
"Failed: $($failed -join ', ')" | Add-Content $LOG_FILE