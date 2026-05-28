#!/bin/bash
# PixelPivot Air-Gap Export Script

EXPORT_DIR="out/airgap_bundle"
mkdir -p "$EXPORT_DIR"

echo "🚀 [PIXELPIVOT] Exporting images for air-gapped deployment..."

# 1. API Image
echo "📦 Exporting API engine..."
docker save pixelpivot_batch-pixelpivot-batch-api:latest | gzip > "$EXPORT_DIR/pixelpivot-api.tar.gz"

# 2. GUI Image
echo "📦 Exporting GUI terminal..."
docker save pixelpivot_batch-pixelpivot-batch-gui:latest | gzip > "$EXPORT_DIR/pixelpivot-gui.tar.gz"

# 3. CLI Image
echo "📦 Exporting CLI runner..."
docker save pixelpivot_batch-pixelpivot-cli:latest | gzip > "$EXPORT_DIR/pixelpivot-cli.tar.gz"

# 4. Cleanup
echo "✅ Export complete! Files are in $EXPORT_DIR"
echo "To load on the target machine:"
echo "   docker load < pixelpivot-api.tar.gz"
echo "   docker load < pixelpivot-gui.tar.gz"
echo "   docker load < pixelpivot-cli.tar.gz"
