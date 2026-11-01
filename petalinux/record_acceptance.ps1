param(
    [Parameter(Mandatory=$true)][string]$Specimen,
    [Parameter(Mandatory=$true)][string]$Placement,
    [Parameter(Mandatory=$true)][ValidateSet('UNDRILLED_REFERENCE','HOLE_TARGET','NORMAL_CONTROL')][string]$Role,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$Hole,
    [Nullable[double]]$NominalDepth,
    [int]$Frames=5,
    [int]$Deadline=12,
    [int]$EthernetIfIndex=24,
    [switch]$RecouplingConfirmed
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$python=Join-Path $root 'firmware\inspection\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare the project Python environment first' }
$arguments=@((Join-Path $PSScriptRoot 'capture_acceptance_batch.py'),'--specimen',$Specimen,'--placement',$Placement,
    '--role',$Role,'--out',$OutputDirectory,'--frames',"$Frames",'--deadline',"$Deadline")
if ($Hole) { $arguments += @('--hole',$Hole) }
if ($null -ne $NominalDepth) { $arguments += @('--nominal-depth',$NominalDepth.ToString([Globalization.CultureInfo]::InvariantCulture)) }
if ($RecouplingConfirmed) { $arguments += '--recoupling-confirmed' }
$credential=& wsl.exe -d Ubuntu-22.04-PetaLinux -u fpga -- cat /home/fpga/logs/md1213_linux_net/jtag-smoke/operator.token
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the existing private board credential' }
$credential=($credential -join '').Trim()
if ($credential -notmatch '^[A-Za-z0-9_-]{32,128}$') { throw 'Invalid private credential; nothing was sent' }
$previousToken=$env:UT_BOARD_OPERATOR_TOKEN
$previousInterface=$env:UT_BOARD_ETHERNET_IFINDEX
try {
    $env:UT_BOARD_OPERATOR_TOKEN=$credential
    $env:UT_BOARD_ETHERNET_IFINDEX="$EthernetIfIndex"
    $credential=$null
    & $python @arguments
    $result=$LASTEXITCODE
} finally {
    $env:UT_BOARD_OPERATOR_TOKEN=$previousToken
    $env:UT_BOARD_ETHERNET_IFINDEX=$previousInterface
    $credential=$null
}
exit $result
