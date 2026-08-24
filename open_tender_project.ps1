param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"
$codexBinRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
$codexCli = Get-ChildItem -LiteralPath $codexBinRoot -Filter "codex.exe" -File -Recurse |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($null -eq $codexCli) {
    throw "Codex desktop CLI was not found under $codexBinRoot"
}

$cleanProjectPath = $ProjectPath.Trim().Trim('"')
$resolvedProjectPath = (Resolve-Path -LiteralPath $cleanProjectPath).Path
& $codexCli.FullName app $resolvedProjectPath
exit $LASTEXITCODE
