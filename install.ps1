$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -X utf8 $installer @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -X utf8 $installer @args
} else {
    throw 'Install Python 3.10+ first, then rerun this installer.'
}
exit $LASTEXITCODE
