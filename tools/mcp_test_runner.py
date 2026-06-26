# /// script
# dependencies = [
#   "mcp",
# ]
# ///
import re
import subprocess
import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Test Runner")

def get_venv_pytest(git_root):
    """Locates pytest inside the virtual environment."""
    venv_paths = [
        os.path.join(git_root, ".venv", "Scripts", "pytest.exe"),
        os.path.join(git_root, "venv", "Scripts", "pytest.exe"),
        os.path.join(git_root, ".venv", "bin", "pytest"),
        os.path.join(git_root, "venv", "bin", "pytest")
    ]
    for p in venv_paths:
        if os.path.exists(p):
            return p
            
    # Fallback to python -m pytest
    python_paths = [
        os.path.join(git_root, ".venv", "Scripts", "python.exe"),
        os.path.join(git_root, "venv", "Scripts", "python.exe"),
        os.path.join(git_root, ".venv", "bin", "python"),
        os.path.join(git_root, "venv", "bin", "python")
    ]
    for p in python_paths:
        if os.path.exists(p):
            return [p, "-m", "pytest"]
            
    return ["pytest"]

def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def compact_pytest_output(stdout, stderr, exit_code):
    stdout = strip_ansi_codes(stdout)
    stderr = strip_ansi_codes(stderr)
    
    lines = stdout.splitlines()
    
    # 1. Extract summary line
    summary = ""
    for line in reversed(lines):
        trimmed = line.strip()
        if trimmed and trimmed.startswith("==") and trimmed.endswith("=="):
            summary = trimmed.strip(" =")
            break
    if not summary:
        for line in reversed(lines):
            trimmed = line.strip()
            if trimmed:
                summary = trimmed
                break

    if exit_code == 0:
        output_parts = [
            "=== TEST RUN SUMMARY ===",
            "Status: PASSED",
            f"Summary: {summary or 'All tests passed'}",
            "========================"
        ]
        return "\n".join(output_parts)

    # 2. Extract failed/errored tests from the short test summary info section
    failed_tests = []
    in_summary_info = False
    for line in lines:
        trimmed = line.strip()
        if re.match(r'^={3,}\s+short test summary info\s+={3,}$', trimmed):
            in_summary_info = True
            continue
        if in_summary_info and re.match(r'^={3,}.*?={3,}$', trimmed):
            in_summary_info = False
            continue
        if in_summary_info and (trimmed.startswith("FAILED ") or trimmed.startswith("ERROR ")):
            parts = trimmed.split()
            if len(parts) > 1:
                failed_tests.append(parts[1])

    # 3. Extract failure details
    in_failures_section = False
    failure_blocks = []
    current_block = []
    
    for line in lines:
        trimmed = line.strip()
        if re.match(r'^={3,}\s+(FAILURES|ERRORS)\s+={3,}$', trimmed):
            in_failures_section = True
            continue
        if in_failures_section and re.match(r'^={3,}\s+(short test summary info|warnings summary|.*?in\s+\d+\.\d+s)\s+={3,}$', trimmed):
            in_failures_section = False
            if current_block:
                failure_blocks.append(current_block)
                current_block = []
            continue
            
        if in_failures_section:
            if re.match(r'^_{3,}.*?_{3,}$', trimmed):
                if current_block:
                    failure_blocks.append(current_block)
                test_name = trimmed.strip("_ ")
                current_block = [f"Failure in {test_name}:"]
            else:
                if current_block:
                    current_block.append(line)
                    
    if current_block:
        failure_blocks.append(current_block)

    compacted_failures = []
    for block in failure_blocks:
        header = block[0]
        body_lines = block[1:]
        
        compacted_body = []
        for line in body_lines:
            trimmed_line = line.strip()
            if not trimmed_line:
                continue
            if line.startswith('E   ') or line.startswith('E '):
                compacted_body.append(line)
            elif re.search(r'\.py:\d+:', line):
                compacted_body.append(line)
            else:
                if line.startswith(' ') or line.startswith('\t'):
                    compacted_body.append(line)

        cleaned_body = []
        for line in compacted_body:
            if not cleaned_body or line != cleaned_body[-1]:
                cleaned_body.append(line)
                
        body_str = "\n".join(cleaned_body)
        compacted_failures.append(f"{header}\n{body_str}")

    output_parts = []
    output_parts.append("=== TEST RUN SUMMARY ===")
    output_parts.append("Status: FAILED")
    output_parts.append(f"Summary: {summary}")
    if failed_tests:
        output_parts.append("Failed/Errored Tests:")
        for t in failed_tests:
            output_parts.append(f"  - {t}")
    output_parts.append("========================\n")

    if compacted_failures:
        output_parts.append("\n" + "\n\n".join(compacted_failures))
    else:
        output_parts.append("\n" + stdout)
        
    if stderr.strip():
        output_parts.append("\n--- Standard Error ---\n" + stderr)
        
    return "\n".join(output_parts)

@mcp.tool()
def run_tests(args: str = "", workdir: str = "") -> str:
    """
    Runs pytest with the given arguments in the specified workdir (or the current directory),
    and returns a compacted token-saving summary. If all tests pass, it returns 'ALL GREEN' with a brief count.
    If any tests fail, it returns an ultra-compact summary of only the failures and traceback lines.
    """
    git_root = os.getcwd()
    if workdir:
        git_root = os.path.abspath(workdir)
        
    pytest_bin = get_venv_pytest(git_root)
    cmd = [pytest_bin] if isinstance(pytest_bin, str) else list(pytest_bin)
    
    if args:
        cmd.extend(args.split())
        
    res = subprocess.run(cmd, cwd=git_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return compact_pytest_output(res.stdout, res.stderr, res.returncode)

if __name__ == "__main__":
    mcp.run()
