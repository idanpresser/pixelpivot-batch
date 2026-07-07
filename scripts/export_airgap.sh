#!/bin/bash
# PixelPivot Air-Gap Export Script

set -euo pipefail

EXPORT_DIR="${1:-out/airgap_bundle}"
mkdir -p "$EXPORT_DIR"

echo "[PIXELPIVOT] Exporting images for air-gapped deployment..."
echo "  Output dir: $EXPORT_DIR"

# All 3 app images share identical base layers — save together so layers
# are written once instead of three times (~1 GB vs ~3 GB).
echo "  Exporting app images (combined)..."
docker save \
    pixelpivot_batch-pixelpivot-batch-api:latest \
    pixelpivot_batch-pixelpivot-batch-gui:latest \
    pixelpivot_batch-pixelpivot-cli:latest \
    | gzip > "$EXPORT_DIR/pixelpivot-app.tar.gz"

echo "  Exporting postgres:16..."
docker save postgres:16 | gzip > "$EXPORT_DIR/postgres.tar.gz"

echo "Export complete. Files in $EXPORT_DIR:"
ls -lh "$EXPORT_DIR"/*.tar.gz
echo ""
echo "Load on target machine:"
echo "   docker load < pixelpivot-app.tar.gz   # loads all 3 app images"
echo "   docker load < postgres.tar.gz"
