# Multi-arch build helper for PowerShell (wraps docker buildx)
Param(
  [string]$Branch,
  [string]$ExtraTags = "",
  [switch]$NoPush,
  [string]$Platforms = "linux/amd64,linux/arm64",
  [string]$ImageBase = "ghcr.io/waariswallie/nilm-app"
)

if (-not $Branch) { $Branch = (git rev-parse --abbrev-ref HEAD) }
$SafeBranch = $Branch -replace '[^a-zA-Z0-9_-]','-'
$ShortSha = (git rev-parse --short=7 HEAD)

Write-Host "Branch:      $Branch"
Write-Host "SafeBranch:  $SafeBranch"
Write-Host "Short SHA:   $ShortSha"
Write-Host "ImageBase:   $ImageBase"
Write-Host "Platforms:   $Platforms"

$tags = @("$ImageBase:$SafeBranch-latest", "$ImageBase:$SafeBranch-$ShortSha")
if ($ExtraTags) { $ExtraTags.Split(',') | ForEach-Object { if ($_ -ne '') { $tags += $_ } } }
Write-Host "Tags:        $($tags -join ', ')"

if (-not (docker buildx ls | Select-String -Quiet 'multiarch-nilm')) {
  docker buildx create --name multiarch-nilm --use | Out-Null
}
docker buildx inspect multiarch-nilm | Out-Null

$buildArgs = @('--platform', $Platforms, '-f', 'Dockerfile', '--build-arg', "VCS_REF=$(git rev-parse HEAD)")
foreach ($t in $tags) { $buildArgs += @('-t', $t) }
if ($NoPush) { $buildArgs += '--load' } else { $buildArgs += '--push' }

Write-Host '> Building...' -ForegroundColor Cyan
docker buildx build . @buildArgs

if (-not $NoPush) {
  Write-Host '> Manifest inspect' -ForegroundColor Cyan
  docker buildx imagetools inspect "$ImageBase:$SafeBranch-latest" | Select-String 'Name|Platform' || $true
}

Write-Host 'Done.' -ForegroundColor Green
