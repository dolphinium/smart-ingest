# main.py
import argparse
import os
import sys
import json
import re
import subprocess
import tempfile
import shutil
from typing import List, Optional, Set, Tuple, Union

import google.generativeai as genai
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, ValidationError
from gitingest import ingest_async
import asyncio

# Load environment variables from .env file
load_dotenv()

# Configure the Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY not found in environment variables or .env file.")
    print("You'll need to provide it with the --api-key option for auto-exclude.")

# --- Configuration ---
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash" # Use a reliable and fast model
# --- End Configuration ---


# System prompt for the Gemini API
SYSTEM_PROMPT = """
You are an expert assistant specialized in preparing code repositories for analysis by Large Language Models (LLMs) using tools like `gitingest`. Your sole task is to analyze a provided directory structure (given as text) and generate a **single line** string containing comma-separated patterns (glob patterns or specific paths relative to the repository root) for exclusion, **based *only* on items found within that specific structure**.

**Goal:** Identify and list patterns for files and directories *present in the input structure* that match common exclusion criteria (dependencies, compiled code, VCS, IDE config, large data/assets, lock files, environment files etc.) and are generally unnecessary or detrimental for LLM codebase understanding. Generate paths relative to the root of the provided structure.

**Exclusion Guidelines & Process:**
1.  **Analyze Input:** Carefully examine the provided directory structure, noting the exact names and locations of all files and directories.
2.  **Identify Candidates for Exclusion:** Look for items *within the input structure* that fall into common exclusion categories:
    *   Dependency directories (e.g., `node_modules/`, `venv/`, `.venv/`, `env/`, `vendor/`, `packages/`)
    *   Compiled/Generated files/directories (e.g., `__pycache__/`, `*.pyc`, `*.pyo`, `build/`, `dist/`, `target/`, `out/`, `*.class`, `*.o`, `*.obj`, `*.dll`, `*.so`)
    *   Version control system metadata (e.g., `.git/`, `.svn/`, `.hg/`)
    *   Package manager lock files (e.g., `package-lock.json`, `yarn.lock`, `poetry.lock`, `composer.lock`, `Gemfile.lock`) - Exclude if they tend to be very large or less critical for understanding core logic.
    *   IDE/Editor configuration files/directories (e.g., `.vscode/`, `.idea/`, `*.sublime-project`, `*.sublime-workspace`, `*.swp`, `*.swo`)
    *   Operating System specific files (e.g., `.DS_Store`, `Thumbs.db`)
    *   Test caches/reports (e.g., `.pytest_cache/`, `.tox/`, `coverage/`, `*.log`)
    *   Large binary assets/data (e.g., `*.zip`, `*.tar.gz`, `*.jpg`, `*.png`, `*.mp4`, `data/`) - Use judgement based on typical project structures.
    *   Environment configuration files (e.g., `.env`, `.env.*` - unless they contain crucial *example* configuration).
3.  **Generate Patterns ONLY for Present Items:** For each identified candidate *that actually exists in the input structure*:
    *   **Use Specific Relative Paths:** If the item is in a specific subdirectory (e.g., `frontend/node_modules/` if `node_modules` is inside `frontend`), use its full relative path from the root.
    *   **Use Direct Names for Root Items:** If the item is directly at the root level (e.g., `.git/`, `venv/`), use its direct name.
    *   **Use Globs for Widespread Pattern Types (if present):** If files or directories matching a *type* known to appear widely (like `__pycache__` directories or `.pyc` files) are present *anywhere* in the structure, use an appropriate glob pattern (e.g., `**/__pycache__/`, `**/*.pyc`). Base the decision to use a glob on the *nature* of the item (caches, compiled files often appear nested). Prioritize specific paths if the item appears only once or twice in specific locations.
4.  **Compile Final List:** Combine the generated patterns for all *present* excludable items into a single comma-separated string. Ensure patterns for directories end with `/`.
5.  **Strict Inclusion Rule:** **Crucially, do *not* include a pattern for any file or directory (e.g., `.vscode/`, `build/`, `node_modules/`) if it is *not explicitly listed* in the provided directory structure input.** Check the input structure carefully before adding a pattern.

**Important:** Return ONLY the comma-separated list of patterns on a single line. Do not include explanations, apologies, or code block markers (like ```).

Example input structure:
my_project/
├── .git/
├── src/
│   ├── main.py
│   └── utils.py
│   └── __pycache__/
│       └── utils.cpython-39.pyc
├── node_modules/
│   └── some_package/
├── tests/
│   └── test_main.py
├── venv/
│   └── ...
├── .env
├── package.json
└── README.md

Example output format: `.git/, node_modules/, venv/, **/__pycache__/, **/*.pyc, .env`
"""

class ExcludePatterns(BaseModel):
    """Pydantic model for exclude patterns."""
    patterns: List[str] = Field(
        description="List of exclude patterns as strings",
        example=[".git/", "node_modules/", "**/*.pyc"]
    )

    @field_validator('patterns', mode='before')
    @classmethod
    def parse_and_clean_patterns(cls, v):
        """Validate and clean the patterns from string or list."""
        raw_patterns = []
        if isinstance(v, str):
            # Remove potential code block markers and surrounding whitespace
            cleaned_str = re.sub(r'(^```[a-zA-Z]*\s*|\s*```$)', '', v, flags=re.MULTILINE).strip()
            # Split comma-separated string into list
            raw_patterns = [p.strip() for p in cleaned_str.split(',') if p.strip()]
        elif isinstance(v, list):
            raw_patterns = [str(p).strip() for p in v if str(p).strip()]
        else:
            raise ValueError("Patterns must be a string or list")

        # Further clean each pattern (remove extra quotes, etc.)
        # Basic validation: allow common path/glob characters
        valid_patterns = []
        for pattern in raw_patterns:
             # Remove potential leading/trailing quotes sometimes added by LLM
            cleaned_pattern = pattern.strip('\'"` ')
            if cleaned_pattern: # Ensure not empty after cleaning
                 valid_patterns.append(cleaned_pattern)

        return valid_patterns

def create_directory_tree(path: str, depth: int = 0, max_depth: int = 8, prefix: str = "", is_last: bool = True, is_root: bool = True) -> str:
    """
    Create a text representation of the directory structure similar to the `tree` command.

    Args:
        path: The path to the directory or file.
        depth: Current recursion depth.
        max_depth: Maximum recursion depth.
        prefix: The string prefix for the current line (handles indentation and connectors).
        is_last: Whether this item is the last in its parent directory listing.
        is_root: Whether this is the initial call (root node).

    Returns:
        A string representation of the directory structure.
    """
    if depth > max_depth:
        return prefix + "└── [Max depth reached]\n"

    if not os.path.exists(path):
        return prefix + f"└── [Path not found: {os.path.basename(path)}]\n"

    base_name = os.path.basename(path)
    if is_root:
        result = base_name + ("/" if os.path.isdir(path) else "") + "\n"
        connector = "" # No connector for the root itself
    else:
        connector = "└── " if is_last else "├── "
        result = prefix + connector + base_name + ("/" if os.path.isdir(path) else "") + "\n"

    if os.path.isdir(path):
        new_prefix = prefix + ("    " if is_last or is_root else "│   ")
        try:
            items = sorted(os.listdir(path))
        except PermissionError:
            result += new_prefix + "└── [Permission denied]\n"
            return result
        except OSError as e:
             result += new_prefix + f"└── [Error listing: {e}]\n"
             return result

        for i, item in enumerate(items):
            item_path = os.path.join(path, item)
            last = (i == len(items) - 1)
            result += create_directory_tree(
                item_path,
                depth + 1,
                max_depth,
                prefix=new_prefix,
                is_last=last,
                is_root=False # Only the initial call is root
            )

    return result # No rstrip needed as newline is added consistently

async def generate_exclude_patterns(directory_structure: str, api_key: str, model_name: str, retries: int = 3) -> Optional[ExcludePatterns]:
    """
    Use the Gemini API to generate exclude patterns based on the directory structure.

    Args:
        directory_structure: Text representation of the directory structure.
        api_key: Gemini API key.
        model_name: Name of the Gemini model to use.
        retries: Number of retry attempts.

    Returns:
        ExcludePatterns object or None if generation fails.
    """
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        print(f"Error configuring Gemini API: {e}", file=sys.stderr)
        return None

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.1, # Low temp for consistency, but allow slight variation
            "max_output_tokens": 1024,
        }
    )

    prompt = f"Analyze the following directory structure and generate a single comma-separated line of exclude patterns based *only* on the items present. Follow the exclusion guidelines strictly.\n\nDirectory structure:\n```\n{directory_structure}\n```\n\nExclude patterns:"

    for attempt in range(retries):
        print(f"Calling Gemini API (Attempt {attempt + 1}/{retries})...")
        try:
            response = model.generate_content(prompt)

            # Debugging: Print raw response text
            raw_text = response.text.strip()
            print(f"Gemini Raw Response (Attempt {attempt+1}):\n---\n{raw_text}\n---")

            # Use Pydantic model for parsing and validation
            try:
                parsed_patterns = ExcludePatterns(patterns=raw_text)
                if parsed_patterns.patterns: # Ensure we got some patterns
                    return parsed_patterns
                else:
                    print(f"Warning: Gemini returned an empty pattern list on attempt {attempt + 1}.")
                    # Optionally retry if the response was empty but valid format

            except ValidationError as e:
                print(f"Attempt {attempt + 1}: Failed to parse/validate response: {e}")
                print(f"Raw response was: {raw_text}")
                # Don't retry automatically on validation errors unless it's likely transient

            except Exception as e: # Catch other potential parsing errors
                print(f"Attempt {attempt + 1}: Unexpected error parsing response: {str(e)}")
                print(f"Raw response was: {raw_text}")

        except genai.types.generation_types.BlockedPromptException as e:
             print(f"Attempt {attempt + 1}: Gemini API call failed due to blocked prompt: {e}", file=sys.stderr)
             # This is unlikely to succeed on retry with the same prompt
             break
        except Exception as e:
            print(f"Attempt {attempt + 1}: Error calling Gemini API: {str(e)}", file=sys.stderr)
            # Wait a bit before retrying for transient issues
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt) # Exponential backoff

    print("Failed to generate valid exclude patterns from Gemini API after all attempts.", file=sys.stderr)
    return None

async def clone_repo(repo_url: str, target_dir: str, branch: Optional[str] = None) -> bool:
    """Clones a Git repository using the git command line."""
    print(f"Cloning '{repo_url}'" + (f" (branch: {branch})" if branch else "") + f" into '{target_dir}'...")
    cmd = ["git", "clone", "--depth", "1", "--quiet"] # Shallow clone for speed
    if branch:
        cmd.extend(["-b", branch])
    cmd.append(repo_url)
    cmd.append(target_dir)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            print("Cloning successful.")
            return True
        else:
            print(f"Error cloning repository (Return Code: {process.returncode}):", file=sys.stderr)
            print(f"Stderr: {stderr.decode().strip()}", file=sys.stderr)
            return False
    except FileNotFoundError:
        print("Error: 'git' command not found. Please ensure Git is installed and in your PATH.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"An unexpected error occurred during cloning: {e}", file=sys.stderr)
        return False

async def main():
    parser = argparse.ArgumentParser(description="Enhanced GitIngest with Gemini-powered automatic exclude pattern generation")
    parser.add_argument("source", type=str, help="Source directory or repository URL")
    parser.add_argument("--output", "-o", help="Output file path (default: digest-{repo_name}.txt or digest-{dir_name}.txt)")
    parser.add_argument("--max-size", "-s", type=int, default=10*1024*1024, help="Maximum file size to process in bytes (default: 10MB)")
    parser.add_argument("--exclude-pattern", "-e", action="append", default=[], help="Additional patterns to exclude (can be specified multiple times)")
    parser.add_argument("--include-pattern", "-i", action="append", help="Patterns to include (overrides excludes, can be specified multiple times)")
    parser.add_argument("--branch", "-b", help="Branch to clone and ingest if source is a URL")
    parser.add_argument("--api-key", help="Gemini API key (overrides GEMINI_API_KEY environment variable)")
    parser.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL, help=f"Gemini model for pattern generation (default: {DEFAULT_GEMINI_MODEL})")
    parser.add_argument("--no-auto-exclude", action="store_true", help="Disable automatic exclude pattern generation via Gemini")
    parser.add_argument("--max-depth", type=int, default=8, help="Maximum directory traversal depth for analysis tree (default: 8)")
    parser.add_argument("--dry-run", action="store_true", help="Generate and show exclude patterns without performing ingestion")
    parser.add_argument("--show-tree", action="store_true", help="Show the directory tree used for analysis")
    parser.add_argument("--retries", type=int, default=3, help="Number of Gemini API call retries (default: 3)")

    args = parser.parse_args()

    # Determine API key
    api_key = args.api_key or GEMINI_API_KEY
    use_auto_exclude = not args.no_auto_exclude and bool(api_key)

    if not args.no_auto_exclude and not api_key:
        print("Warning: --no-auto-exclude not set, but no Gemini API key found. Disabling automatic excludes.")

    source = args.source
    is_url = source.startswith("http://") or source.startswith("https://") or source.startswith("git@")
    local_source_path: Optional[str] = None
    temp_dir_obj = None # To hold the TemporaryDirectory object for cleanup

    try:
        # --- Stage 1: Prepare Local Source Path ---
        if is_url:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="smart_ingest_clone_")
            local_source_path = temp_dir_obj.name
            clone_successful = await clone_repo(source, local_source_path, args.branch)
            if not clone_successful:
                # Error messages handled within clone_repo
                sys.exit(1)
            repo_name = source.split('/')[-1].replace('.git', '') # Basic repo name extraction
            default_output = f"digest-{repo_name}.txt"
        else:
            local_source_path = os.path.abspath(source)
            if not os.path.exists(local_source_path):
                print(f"Error: Local source path does not exist: {local_source_path}", file=sys.stderr)
                sys.exit(1)
            if not os.path.isdir(local_source_path):
                 # Allow single file analysis if needed by gitingest? For now, focus on dirs for tree analysis
                 print(f"Warning: Local source path is a file, not a directory. Tree analysis for auto-exclude might be limited.", file=sys.stderr)
                 # Consider if tree analysis should be skipped or handled differently for single files
            dir_name = os.path.basename(local_source_path)
            default_output = f"digest-{dir_name}.txt"
            repo_name = dir_name # Use dir name for context

        # --- Stage 2: Generate Directory Tree (if directory exists) ---
        directory_tree = None
        if os.path.isdir(local_source_path):
            print(f"Analyzing directory structure: {local_source_path}")
            directory_tree = create_directory_tree(local_source_path, max_depth=args.max_depth).strip()
            if args.show_tree:
                print("\n--- Directory Tree ---")
                print(directory_tree)
                print("--- End Tree ---\n")
        elif use_auto_exclude:
             print("Warning: Source is not a directory. Skipping directory tree generation and automatic exclude pattern generation.", file=sys.stderr)
             use_auto_exclude = False # Can't generate patterns without a tree

        # --- Stage 3: Generate Exclude Patterns ---
        exclude_patterns = set(args.exclude_pattern) # Start with manually provided patterns

        if use_auto_exclude and directory_tree:
            print(f"Generating intelligent exclude patterns using Gemini ({args.gemini_model})...")
            auto_generated_patterns = await generate_exclude_patterns(
                directory_tree,
                api_key, # Already checked this exists if use_auto_exclude is True
                model_name=args.gemini_model,
                retries=args.retries
            )

            if auto_generated_patterns and auto_generated_patterns.patterns:
                print("\nAutomatically generated exclude patterns:")
                # Use a temporary set to avoid printing duplicates if LLM repeats itself
                unique_auto_patterns = set(auto_generated_patterns.patterns)
                for pattern in sorted(list(unique_auto_patterns)): # Sort for consistent display
                    print(f"  - {pattern}")
                exclude_patterns.update(unique_auto_patterns)
            else:
                print("\nFailed to generate automatic exclude patterns or none were suggested. Using manual patterns only.")
        elif use_auto_exclude and not directory_tree:
            # This case was handled above, just confirming flow.
            pass # Warning already printed
        else:
            print("Skipping automatic exclude pattern generation.")

        print("\nFinal Exclude Patterns:")
        if exclude_patterns:
            for pattern in sorted(list(exclude_patterns)):
                print(f"  - {pattern}")
        else:
            print("  (None)")

        # --- Stage 4: Dry Run or Ingest ---
        if args.dry_run:
            print("\nDry run requested. Exiting without performing ingestion.")
            return # Exit after showing patterns

        if not local_source_path:
             print("Error: Cannot proceed without a valid local source path.", file=sys.stderr)
             sys.exit(1)

        # Prepare parameters for gitingest
        include_patterns_set = set(args.include_pattern) if args.include_pattern else None
        output_file = args.output or default_output # Use default if not provided

        print(f"\nRunning GitIngest on: {local_source_path}")
        print(f"Output file: {output_file}")
        print(f"Max file size: {args.max_size} bytes")
        if include_patterns_set:
            print(f"Include patterns: {', '.join(sorted(list(include_patterns_set)))}")

        try:
            summary, _, _ = await ingest_async(
                source=local_source_path,
                max_file_size=args.max_size,
                include_patterns=include_patterns_set, # Pass set or None
                exclude_patterns=exclude_patterns if exclude_patterns else None, # Pass set or None
                branch=args.branch if is_url else None,
                output=output_file
            )

            print(f"\nAnalysis complete! Output written to: {output_file}")
            print("\nSummary:")
            print(summary)

        except FileNotFoundError as e:
             print(f"\nError running GitIngest: A required file or directory was not found.", file=sys.stderr)
             print(f"Details: {e}", file=sys.stderr)
             print("Please ensure the source path and patterns are correct.", file=sys.stderr)
             sys.exit(1)
        except Exception as e:
            print(f"\nError running GitIngest: {str(e)}", file=sys.stderr)
            # Consider printing traceback for unexpected errors
            # import traceback
            # traceback.print_exc()
            sys.exit(1)

    finally:
        # --- Stage 5: Cleanup ---
        if temp_dir_obj:
            print(f"Cleaning up temporary directory: {temp_dir_obj.name}")
            # TemporaryDirectory handles removal on exit/garbage collection,
            # but explicit cleanup can be added if needed (e.g., shutil.rmtree)
            # However, letting the context manager handle it is generally safer.
            temp_dir_obj.cleanup()


if __name__ == "__main__":
    # Fix for Windows asyncio event loop policy if needed
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())