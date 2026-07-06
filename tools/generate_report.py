#!/usr/bin/env python3
"""CLI tool for generating self-contained HTML reports for conversions."""

import os
import sys
import argparse
from pathlib import Path

# Add project root to python path to import app modules correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.reports.generator import generate_report_for_run, generate_report_for_dirs
from app.batch_api.models import _resolve_path

def main():
    parser = argparse.ArgumentParser(
        description="Generate a self-contained HTML report for image conversion runs or directories."
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-id",
        type=int,
        help="Generate report for a specific database batch run ID."
    )
    group.add_argument(
        "--dirs",
        action="store_true",
        help="Generate folder-wide report from source and target directories."
    )

    # Directory parameters used in folder-wide or override modes
    parser.add_argument(
        "--source",
        type=str,
        help="Source directory (required if --dirs is specified)."
    )
    parser.add_argument(
        "--target",
        type=str,
        help="Target directory (required if --dirs is specified)."
    )

    parser.add_argument(
        "--output",
        type=str,
        help="Output HTML file path. Defaults to target directory."
    )

    args = parser.parse_args()

    if args.dirs:
        if not args.source or not args.target:
            parser.error("--source and --target directories are required when --dirs is used.")
        
        abs_src = _resolve_path(args.source)
        abs_tgt = _resolve_path(args.target)
        
        output_file = args.output
        if not output_file:
            output_file = os.path.join(abs_tgt, "batch_report_summary.html")
            
        print(f"Generating folder-wide summary report...")
        print(f"  Source dir: {abs_src}")
        print(f"  Target dir: {abs_tgt}")
        
        try:
            generate_report_for_dirs(abs_src, abs_tgt, output_file)
            print(f"✓ Report generated successfully at: {output_file}")
        except Exception as e:
            print(f"❌ Error generating report: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.run_id:
        print(f"Retrieving details for run ID #{args.run_id}...")
        
        # We need to query DB first to find the default target dir if output is not specified
        from app.core.db.connection import get_connection
        from app.core.db.repositories.batch import BatchRepository
        
        try:
            with get_connection() as conn:
                repo = BatchRepository()
                run = repo.get_run(conn, args.run_id)
                if not run:
                    print(f"❌ Error: Batch run ID {args.run_id} not found in database.", file=sys.stderr)
                    sys.exit(1)
                
                target_dir = run["target_dir"]
        except Exception as e:
            print(f"❌ Database error: {e}", file=sys.stderr)
            sys.exit(1)
            
        abs_tgt = _resolve_path(target_dir)
        output_file = args.output
        if not output_file:
            output_file = os.path.join(abs_tgt, f"batch_report_run_{args.run_id}.html")

        print(f"Generating batch run report...")
        print(f"  Run ID: {args.run_id}")
        print(f"  Target dir: {abs_tgt}")
        
        try:
            generate_report_for_run(args.run_id, output_file)
            print(f"✓ Report generated successfully at: {output_file}")
        except Exception as e:
            print(f"❌ Error generating report: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
