"""Code to Markdown — converts a codebase into a single Markdown document."""

import os
import sys
from pathlib import Path

# --- Configuration ---
IGNORE_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build', '.idea', '.vscode'}
IGNORE_EXTS = {'.exe', '.pyc', '.pth', '.bin', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.pdf', '.zip', '.tar', '.gz'}

EXT_TO_LANG = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript', '.html': 'html',
    '.css': 'css', '.sh': 'bash', '.bash': 'bash', '.json': 'json',
    '.md': 'markdown', '.cpp': 'cpp', '.c': 'c', '.rs': 'rust',
    '.go': 'go', '.yml': 'yaml', '.yaml': 'yaml', '.sql': 'sql'
}

def get_tree(root_dir, output_file_name, script_file_name, prefix=""):
    """Generate a tree-view representation of directory structure.

    Args:
        root_dir: Root directory path.
        output_file_name: Name of the output file to exclude.
        script_file_name: Name of the script file to exclude.
        prefix: Indentation prefix for recursive calls.

    Returns:
        List of tree-formatted strings representing the directory structure.
    """
    tree = []
    # Get all items, filter out ignored dirs and the script/output files themselves
    paths = [
        p for p in sorted(Path(root_dir).iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if p.name not in IGNORE_DIRS 
        and p.name != output_file_name 
        and p.name != script_file_name
    ]
    
    for i, path in enumerate(paths):
        connector = "└── " if i == len(paths) - 1 else "├── "
        tree.append(f"{prefix}{connector}{path.name}")
        
        if path.is_dir():
            extension = "    " if i == len(paths) - 1 else "│   "
            tree.extend(get_tree(path, output_file_name, script_file_name, prefix + extension))
            
    return tree

def convert_codebase_to_md(target_path):
    """Convert a codebase into a single Markdown file with directory structure and file contents.

    Args:
        target_path: Root directory to convert.
    """
    root = Path(target_path).resolve()
    if not root.is_dir():
        print(f"Error: {target_path} is not a valid directory.")
        return

    # Names of files to exclude from the backup
    output_filename = f"{root.name}.md"
    script_filename = Path(__file__).name

    with open(output_filename, "w", encoding="utf-8") as md_file:
        # 1. Write Title
        md_file.write(f"# Codebase: {root.name}\n\n")
        
        # 2. Write Directory Structure
        md_file.write("## Directory Structure\n")
        md_file.write("```text\n")
        md_file.write(f"{root.name}/\n")
        tree_lines = get_tree(root, output_filename, script_filename)
        md_file.write("\n".join(tree_lines))
        md_file.write("\n```\n\n---\n\n")
        
        # 3. Write File Contents
        md_file.write("## File Contents\n\n")
        
        for file_path in root.rglob("*"):
            # SKIP LOGIC:
            # 1. Skip if it's the output markdown file
            if file_path.name == output_filename:
                continue
            # 2. Skip if it's this script itself
            if file_path.name == script_filename:
                continue
            # 3. Skip if in an ignored directory
            if any(ignored in file_path.parts for ignored in IGNORE_DIRS):
                continue
            # 4. Skip if it's a directory or binary/ignored extension
            if file_path.is_dir() or file_path.suffix.lower() in IGNORE_EXTS:
                continue
            
            relative_path = file_path.relative_to(root)
            lang = EXT_TO_LANG.get(file_path.suffix.lower(), "")
            
            md_file.write(f"### File: `{relative_path}`\n")
            md_file.write(f"```{lang}\n")
            
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                md_file.write(content)
            except Exception as e:
                md_file.write(f"[Error reading file: {e}]")
            
            md_file.write("\n```\n\n")

    print(f"Success! Markdown file created: {output_filename}")

if __name__ == "__main__":
    folder_to_scan = sys.argv[1] if len(sys.argv) > 1 else "."
    convert_codebase_to_md(folder_to_scan)