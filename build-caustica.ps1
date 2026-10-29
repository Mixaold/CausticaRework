# Builds caustica-src (Fabric jar) with MSVC, Slang, DLSS and Vulkan SDKs.
param([ValidateSet('prep', 'jar', 'all', 'sharc')][string]$Step = 'all', [switch]$Offline, [switch]$NoSharc)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$vcvars = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
foreach ($line in (cmd /c "`"$vcvars`" >nul 2>&1 && set")) {
    if ($line -match '^([^=]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] }
}
$env:VULKAN_SDK = (Get-ChildItem C:\VulkanSDK -Directory | Sort-Object Name | Select-Object -Last 1).FullName
$env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-25.0.3.9-hotspot'
$env:SLANG_SDK = Join-Path $root 'sdk\slang'
$env:DLSS_SDK = Join-Path $root 'sdk\DLSS'
$env:GRADLE_USER_HOME = Join-Path $root '.gradle-home'
# NVIDIA SHaRC 1.8.0.0 (radiance cache, NVIDIA RTX SDK license): headers stay outside the jar; the jar records this
# path and the game compiles them at run time. `.\build-caustica.ps1 sharc` downloads the pinned commit once.
$sharcSdk = Join-Path $root 'sdk\sharc'
$sharcCommit = 'e19ccacd511f42a3df6f850052d508c13c9e9737'
$env:Path = "$env:APPDATA\Python\Python314\Scripts;$env:JAVA_HOME\bin;$env:VULKAN_SDK\Bin;$env:Path"
Set-Location (Join-Path $root 'caustica-src')
$gradle = Join-Path (Get-Location) 'gradlew.bat'
$ErrorActionPreference = 'Continue'  # PS 5.1 turns native stderr into terminating errors under Stop
$extra = @(if ($Offline) { '--offline' })
if ($Step -eq 'sharc') {
    $zip = Join-Path $env:TEMP "sharc-$sharcCommit.zip"
    Invoke-WebRequest "https://github.com/NVIDIA-RTX/SHARC/archive/$sharcCommit.zip" -OutFile $zip
    $unpacked = Join-Path $env:TEMP "sharc-$sharcCommit"
    if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
    Expand-Archive $zip -DestinationPath $unpacked
    if (Test-Path $sharcSdk) { Remove-Item $sharcSdk -Recurse -Force }
    Move-Item (Get-ChildItem $unpacked -Directory | Select-Object -First 1).FullName $sharcSdk
    Write-Host "SHaRC 1.8.0.0 -> $sharcSdk (Gradle checks the header hashes on the next jar build)"
    exit 0
}
if (-not $NoSharc -and (Test-Path (Join-Path $sharcSdk 'include\SharcCommon.h'))) {
    $extra += "-PsharcSdk=$sharcSdk"
} else {
    Write-Host 'SHaRC SDK not used (run .\build-caustica.ps1 sharc, or -NoSharc was given): radiance cache stays off'
}
if ($Step -in 'prep', 'all') {
    & $gradle --no-daemon @extra ':packages:nvidia-nrd:prepareNrdNativeDependencies'
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
if ($Step -in 'jar', 'all') {
    # A kept daemon skips Loom's configuration on the next build; it stops itself after 30 idle minutes
    # (org.gradle.daemon.idletimeout in .gradle-home\gradle.properties) to give the memory back to the game.
    & $gradle @extra '-Ploader=fabric' build -x check
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
