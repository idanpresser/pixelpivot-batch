#!/usr/bin/env python3
import os
import re
import shutil
import argparse

def main():
    parser = argparse.ArgumentParser(
        description="Find and delete duplicate '- Copy' files and folders (avoiding recycle bin)."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory to scan (default: current directory)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete files/folders (default is dry-run mode)"
    )
    args = parser.parse_args()

    root_dir = os.path.abspath(args.root)
    dry_run = not args.execute

    print(f"Scanning directory: {root_dir}")
    if dry_run:
        print("Running in DRY-RUN mode. No files or folders will be deleted.")
        print("To execute deletions, run with: python scripts/cleanup_duplicates.py --execute\n")
    else:
        print("Running in EXECUTE mode. Matching duplicate files/folders will be deleted permanently.\n")

    # Pattern to match ' - copy' suffix (case insensitive) at the end of a string.
    # Requires at least one space before the hyphen to match standard Windows duplicate
    # naming conventions and avoid false positives on names like 'some-copy.txt'.
    pattern = re.compile(r"^(.*?)(?:\s+-\s+[Cc][Oo][Pp][Yy])\s*$")

    deleted_files = 0
    deleted_dirs = 0
    skipped_files = 0
    skipped_dirs = 0

    def get_original_file_name(filename):
        current_name = filename
        while True:
            base, ext = os.path.splitext(current_name)
            match = pattern.match(base)
            if match:
                current_name = match.group(1) + ext
            else:
                break
        return current_name if current_name != filename else None

    def get_original_dir_name(dirname):
        current_name = dirname
        while True:
            match = pattern.match(current_name)
            if match:
                current_name = match.group(1)
            else:
                break
        return current_name if current_name != dirname else None

    # Walk top-down to allow skipping folders like .git, and to prune deleted folders from walk
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        # Always ignore .git directory to protect repository integrity
        if ".git" in dirnames:
            dirnames.remove(".git")

        # 1. Process files in the current directory
        for filename in filenames:
            original_filename = get_original_file_name(filename)
            if original_filename:
                copy_filepath = os.path.join(dirpath, filename)
                original_filepath = os.path.join(dirpath, original_filename)

                # Check if the original file exists
                if os.path.exists(original_filepath) and os.path.isfile(original_filepath):
                    if dry_run:
                        print(f"[DRY-RUN] Would delete file: {copy_filepath} (Original exists: {original_filepath})")
                        deleted_files += 1
                    else:
                        try:
                            os.remove(copy_filepath)
                            print(f"Deleted file: {copy_filepath}")
                            deleted_files += 1
                        except Exception as e:
                            print(f"Error deleting file {copy_filepath}: {e}")
                else:
                    # Found a copy pattern, but the original file does not exist
                    skipped_files += 1

        # 2. Process subdirectories in the current directory
        # Iterate over a copy of the list so we can modify dirnames in place
        for dirname in list(dirnames):
            original_dirname = get_original_dir_name(dirname)
            if original_dirname:
                copy_dirpath = os.path.join(dirpath, dirname)
                original_dirpath = os.path.join(dirpath, original_dirname)

                # Check if the original folder exists
                if os.path.exists(original_dirpath) and os.path.isdir(original_dirpath):
                    if dry_run:
                        print(f"[DRY-RUN] Would delete folder: {copy_dirpath} (Original exists: {original_dirpath})")
                        deleted_dirs += 1
                    else:
                        try:
                            shutil.rmtree(copy_dirpath)
                            print(f"Deleted folder: {copy_dirpath}")
                            deleted_dirs += 1
                        except Exception as e:
                            print(f"Error deleting folder {copy_dirpath}: {e}")
                    
                    # Remove from dirnames so os.walk doesn't attempt to traverse into it
                    dirnames.remove(dirname)
                else:
                    # Found a copy pattern, but the original folder does not exist
                    skipped_dirs += 1

    print("\n--- Summary ---")
    action_str = "Would delete" if dry_run else "Deleted"
    print(f"{action_str} {deleted_files} files and {deleted_dirs} folders.")
    if skipped_files > 0 or skipped_dirs > 0:
        print(f"Found {skipped_files} duplicate file(s) and {skipped_dirs} folder(s) without matching originals (left untouched).")
    
    if dry_run:
        print("\nTo apply these changes, run the script with the '--execute' flag:")
        print(f"python scripts/cleanup_duplicates.py --execute")

if __name__ == "__main__":
    main()
