#!/bin/bash
# WSL Docker Startup Script for PixelPivot Batch Engine

# 1. Resolve paths
PROJECT_ROOT="/mnt/i/DEV/PixelPivot_202605/pixelpivot_batch"
cd "$PROJECT_ROOT" || exit

echo "🚀 [PIXELPIVOT] Initializing Linux Docker Stack via WSL..."

# 2. Check for Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed or not in WSL path."
    exit 1
fi

# 3. Build and Run
echo "📦 Building images..."
docker compose build

echo "⚡ Starting services..."
docker compose up -d

echo "📊 Services are initializing:"
echo "   - API: http://localhost:8000"
echo "   - GUI: http://localhost:8503"
echo "   - CLI: docker exec -it pixelpivot_cli bash"

# 4. Follow logs for the API to show progress
docker compose logs -f pixelpivot-batch-api
