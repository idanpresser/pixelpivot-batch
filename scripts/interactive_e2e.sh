#!/bin/bash
# WSL Interactive E2E Verification Script for PixelPivot Batch Engine (v1.0.0)

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine paths dynamically
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPTS_DIR")"
cd "$PROJECT_ROOT" || exit 1

echo -e "${CYAN}========================================================================${NC}"
echo -e "${CYAN}   PIXELPIVOT BATCH ENGINE (v1.0.0) - INTERACTIVE E2E VERIFICATION${NC}"
echo -e "${CYAN}========================================================================${NC}"
echo -e "Workspace: $PROJECT_ROOT"

# Check Docker status
if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}❌ Error: Docker is not running or accessible from WSL.${NC}"
    exit 1
fi

# Check if containers are running, if not offer to start them
if ! docker ps | grep -q "pixelpivot_batch_api"; then
    echo -e "${YELLOW}⚠️ Warning: PixelPivot containers are not running.${NC}"
    read -p "Would you like to build and start the Docker services now? (y/n): " start_choice
    if [[ "$start_choice" =~ ^[Yy]$ ]]; then
        echo -e "${CYAN}📦 Building & Starting Docker Compose Stack...${NC}"
        docker compose build
        docker compose up -d
        echo -e "Waiting 5 seconds for containers to initialize..."
        sleep 5
    else
        echo -e "${RED}Aborting verification. Run this script when Docker is running.${NC}"
        exit 1
    fi
fi

while true; do
    echo
    echo -e "${YELLOW}Please select verification action:${NC}"
    echo "1) Run Full E2E 5-Way Tool Matrix (magick, vips, sharp, ffmpeg, cavif)"
    echo "2) Trigger Custom Batch Run (specify tool, format, sample limit, category)"
    echo "3) Trigger Category Calibration Run"
    echo "4) Show Recent Runs & Telemetry Metrics from Database"
    echo "5) View API container logs"
    echo "6) Exit"
    read -p "Selection (1-6): " choice
    echo

    case $choice in
        1)
            echo -e "${CYAN}🚀 Running Full E2E Matrix via CLI container...${NC}"
            docker exec -it pixelpivot_cli python3 scripts/docker_e2e_test.py
            ;;
        2)
            echo -e "${YELLOW}--- CUSTOM BATCH RUN CONFIGURATION ---${NC}"
            
            # Select Tool
            echo "Available Tools: 1) cavif, 2) sharp, 3) ffmpeg, 4) magick, 5) vips"
            read -p "Select tool (number or name): " tool_sel
            case $tool_sel in
                1|cavif) tool="cavif" ;;
                2|sharp) tool="sharp" ;;
                3|ffmpeg) tool="ffmpeg" ;;
                4|magick) tool="magick" ;;
                5|vips) tool="vips" ;;
                *) tool="cavif" ;;
            esac
            
            # Select Format
            echo "Formats: 1) avif, 2) webp, 3) jxl"
            read -p "Select format (number or name): " fmt_sel
            case $fmt_sel in
                1|avif) format="avif" ;;
                2|webp) format="webp" ;;
                3|jxl) format="jxl" ;;
                *) format="avif" ;;
            esac
            
            # Category
            read -p "Enter Category (default: general, or highRes): " category
            if [ -z "$category" ]; then category="general"; fi
            
            # Sample Limit
            read -p "Enter max images/sample limit (positive integer, or enter for none): " sample
            
            # Input files
            read -p "Filter specific files (comma separated list, e.g. deep.jpg, or enter for all): " infiles
            
            # Prepare Request payload
            payload="{"
            payload+="\"source_dir\": \"/app/test_pics/flat\","
            payload+="\"target_dir\": \"/app/test_pics/out_${tool}_custom\","
            payload+="\"target_format\": [\"$format\"],"
            payload+="\"tool\": [\"$tool\"],"
            payload+="\"category\": [\"$category\"],"
            payload+="\"trigger_type\": \"interactive_wsl\""
            
            if [ -n "$sample" ]; then
                payload+=", \"sample\": $sample"
            fi
            
            if [ -n "$infiles" ]; then
                # format files to json array of strings
                json_files=$(echo "$infiles" | awk -F, '{
                    for (i=1; i<=NF; i++) {
                        gsub(/^[ \t]+|[ \t]+$/, "", $i);
                        printf "\"%s\"%s", $i, (i==NF ? "" : ", ")
                    }
                }')
                payload+=", \"input_files\": [$json_files]"
            fi
            payload+="}"
            
            echo -e "${CYAN}Sending Payload: $payload${NC}"
            
            docker exec pixelpivot_cli python3 -c "
import httpx, time, json
headers = {'X-API-Token': 'dev_secret_token_change_me', 'Content-Type': 'application/json'}
payload = json.loads('$payload')
with httpx.Client(timeout=30.0) as client:
    r = client.post('http://pixelpivot-batch-api:8000/api/v1/batch/start', json=payload, headers=headers)
    if r.status_code != 200:
        print('Error starting run:', r.status_code, r.text)
    else:
        run_id = r.json()['run_id']
        print('Started batch run:', run_id)
        while True:
            status_resp = client.get(f'http://pixelpivot-batch-api:8000/api/v1/batch/status/{run_id}', headers=headers)
            data = status_resp.json()
            status = data.get('status')
            print(f'Status: {status}')
            if status in ('completed', 'failed', 'cancelled'):
                print('Summary:', data.get('summary'))
                break
            time.sleep(2)
"
            ;;
        3)
            echo -e "${YELLOW}--- CATEGORY CALIBRATION RUN ---${NC}"
            read -p "Enter category name to calibrate (e.g. highRes): " cal_cat
            if [ -z "$cal_cat" ]; then cal_cat="highRes"; fi
            
            read -p "Enter tool name to calibrate (default: cavif): " cal_tool
            if [ -z "$cal_tool" ]; then cal_tool="cavif"; fi
            
            read -p "Enter format (default: avif): " cal_fmt
            if [ -z "$cal_fmt" ]; then cal_fmt="avif"; fi
            
            read -p "Enter sample size (default: 30): " cal_sample
            if [ -z "$cal_sample" ]; then cal_sample=30; fi
            
            payload="{"
            payload+="\"source_dir\": \"/app/test_pics/real\","
            payload+="\"target_format\": [\"$cal_fmt\"],"
            payload+="\"tool\": [\"$cal_tool\"],"
            payload+="\"category\": [\"$cal_cat\"],"
            payload+="\"sample\": $cal_sample"
            payload+="}"
            
            echo -e "${CYAN}Sending Calibration Payload: $payload${NC}"
            
            docker exec pixelpivot_cli python3 -c "
import httpx, time, json
headers = {'X-API-Token': 'dev_secret_token_change_me', 'Content-Type': 'application/json'}
payload = json.loads('$payload')
with httpx.Client(timeout=60.0) as client:
    r = client.post('http://pixelpivot-batch-api:8000/api/v1/calibrate', json=payload, headers=headers)
    if r.status_code != 200:
        print('Error starting calibration:', r.status_code, r.text)
    else:
        run_id = r.json()['run_id']
        print('Started calibration run:', run_id)
        while True:
            status_resp = client.get(f'http://pixelpivot-batch-api:8000/api/v1/batch/status/{run_id}', headers=headers)
            data = status_resp.json()
            status = data.get('status')
            print(f'Status: {status}')
            if status in ('completed', 'failed', 'cancelled'):
                print('Summary:', data.get('summary'))
                break
            time.sleep(3)
"
            ;;
        4)
            echo -e "${CYAN}📊 Fetching Run History & Metrics from SQLite...${NC}"
            docker exec pixelpivot_cli sqlite3 -header -column /app/data/pixelpivot.db "
            SELECT r.id AS run_id, r.trigger_type, r.tool, r.category, r.sample, r.status, s.success_count, s.savings_pct, s.duration_ms
            FROM batch_runs r
            LEFT JOIN batch_summary s ON r.id = s.batch_id
            ORDER BY r.id DESC
            LIMIT 15;
"
            ;;
        5)
            echo -e "${CYAN}📋 Displaying last 30 lines of API logs (Ctrl+C to stop)...${NC}"
            docker compose logs --tail=30 -f pixelpivot-batch-api
            ;;
        6)
            echo -e "${GREEN}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid selection. Please choose 1 to 6.${NC}"
            ;;
    esac
done
