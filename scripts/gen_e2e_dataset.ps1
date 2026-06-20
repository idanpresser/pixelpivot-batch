# Populate Z:\pics with E2E test fixtures. Idempotent — skips existing files.
# Usage: .\scripts\gen_e2e_dataset.ps1
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$PicsRoot    = 'Z:\pics',
    [int]   $Required    = 500
)
$ErrorActionPreference = 'Stop'

function Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok([string]$m)   { Write-Host "    OK: $m" -ForegroundColor Green }

# --- 1. Real images ---
Step "Copying real images -> $PicsRoot\real\"
$samplesRoot = Join-Path $ProjectRoot 'image_samples'
if (-not (Test-Path $samplesRoot)) { throw "image_samples\ not found at $samplesRoot" }

$src = Get-ChildItem $samplesRoot -Recurse -File -Include *.jpg,*.jpeg,*.png
if ($src.Count -lt $Required) { throw "Only $($src.Count) images in image_samples\; need $Required" }

$realDir = Join-Path $PicsRoot 'real'
New-Item -ItemType Directory -Force $realDir | Out-Null
$copied = 0
foreach ($f in $src | Select-Object -First $Required) {
    $dest = Join-Path $realDir $f.Name
    if (-not (Test-Path $dest)) { Copy-Item $f.FullName $dest; $copied++ }
}
Ok "Copied $copied new files ($((Get-ChildItem $realDir -File).Count) total)"

# --- 2. Edge cases via embedded Python ---
Step "Generating edge cases -> $PicsRoot\edge_cases\"
$py = Join-Path $ProjectRoot 'python-3.14.5-embed-amd64\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$picsRootEsc = $PicsRoot -replace '\\','\\\\'

$script = @"
import sys
from pathlib import Path
from PIL import Image
import io

ec = Path(r'$PicsRoot') / 'edge_cases'

# truncated: valid JFIF magic, cut at 200 bytes
td = ec / 'truncated'; td.mkdir(parents=True, exist_ok=True)
buf = io.BytesIO(); Image.new('RGB',(100,100),'red').save(buf,'JPEG'); data=buf.getvalue()
for i in range(5):
    p = td / f'truncated_{i:02d}.jpg'
    if not p.exists(): p.write_bytes(data[:200])

# empty
ed = ec / 'empty'; ed.mkdir(parents=True, exist_ok=True)
p = ed / 'empty.jpg'
if not p.exists(): p.write_bytes(b'')

# bad header
bd = ec / 'bad_header'; bd.mkdir(parents=True, exist_ok=True)
p = bd / 'bad_header.jpg'
if not p.exists(): p.write_bytes(b'notanimageatallrandomjunk' * 10)

# huge: 56 MP -> triggers MASSIVE_IMAGE_THRESHOLD=50MP
hd = ec / 'huge'; hd.mkdir(parents=True, exist_ok=True)
p = hd / 'huge_56mp.png'
if not p.exists(): Image.new('L',(8000,7000),128).save(str(p),'PNG')

# tiny
tn = ec / 'tiny'; tn.mkdir(parents=True, exist_ok=True)
p = tn / 'tiny_1x1.png'
if not p.exists(): Image.new('RGB',(1,1),'white').save(str(p),'PNG')

# path edge cases
pathd = ec / 'paths'
for sub,name,color in [
    ('unicode',  'fichier_eleve.jpg', 'blue'),
    ('spaces',   'file with spaces in name.jpg', 'green'),
]:
    d = pathd / sub; d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if not p.exists(): Image.new('RGB',(50,50),color).save(str(p),'JPEG')

# deep nested
dn = pathd / 'deep' / 'a' / 'b' / 'c' / 'd'; dn.mkdir(parents=True, exist_ok=True)
p = dn / 'deep.jpg'
if not p.exists(): Image.new('RGB',(50,50),'cyan').save(str(p),'JPEG')

# long-path edge case omitted: Z: resolves to UNC \\server\share and
# \\?\ cannot prefix mapped-drive UNC paths portably. Long-path fix is
# covered by unit tests (test_long_path_fix.py) and Phase 5 UNC raw path.

print('edge cases OK')
"@

$script | & $py -
Ok "Edge cases generated"

# --- Summary ---
$rc = (Get-ChildItem (Join-Path $PicsRoot 'real') -File).Count
$ec = (Get-ChildItem (Join-Path $PicsRoot 'edge_cases') -Recurse -File).Count
Write-Host "`nDataset ready  real=$rc  edge_cases=$ec  root=$PicsRoot" -ForegroundColor Green
