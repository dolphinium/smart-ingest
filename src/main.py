"""Enhanced GitIngest with Gemini-powered automatic exclude pattern generation."""

import argparse
import asyncio
import os # <-- Add os import
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent))

from gitingest import ingest_async
from core.directory_analyzer import DirectoryAnalyzer
from core.gemini_client import GeminiExcludePatternGenerator
from core.repository_handler import RepositoryHandler
from config import Config, load_config


class SmartIngestApp:
    """Main application class for Smart Ingest."""
    
    def __init__(self, config: Config):
        self.config = config
        self.exclude_patterns: set[str] = set()
        self.repo_handler = RepositoryHandler()
        self.analyzer = DirectoryAnalyzer(max_depth=config.max_depth)
        
        if config.api_key:
            self.gemini_client = GeminiExcludePatternGenerator(
                api_key=config.api_key,
                model_name=config.gemini_model,
                retries=config.retries
            )
        else:
            self.gemini_client = None
            if not config.api_key and not getattr(parse_arguments(), 'no_auto_exclude', False): # Check if auto-exclude was intended
                 print("Warning: No Gemini API key provided. Automatic exclude pattern generation is disabled.", file=sys.stderr)


    async def run(self, args: argparse.Namespace) -> None:
        """Main execution flow."""
        source = args.source
        is_url = self._is_repository_url(source)
        
        local_source_path, temp_dir = await self._prepare_source(source, is_url, args.branch)
        
        try:
            if Path(local_source_path).is_dir(): # Only analyze if it's a directory
                self.exclude_patterns = await self._generate_exclude_patterns(
                    local_source_path, args
                )
            elif not args.no_auto_exclude:
                print(f"Warning: Source '{local_source_path}' is not a directory. Skipping automatic exclude pattern generation.", file=sys.stderr)


            if args.dry_run:
                if not Path(local_source_path).is_dir() and not args.no_auto_exclude:
                    print("Skipping pattern display as source is not a directory and auto-excludes were skipped.")
                elif self.exclude_patterns:
                    self._display_patterns(self.exclude_patterns, "Final Exclude Patterns (Dry Run):")
                else:
                    print("\nFinal Exclude Patterns (Dry Run):")
                    print("  (None - either no patterns generated or auto-generation disabled/skipped)")
                print("\nDry run requested. Exiting without performing ingestion.")
                return
            
            await self._execute_gitingest(local_source_path, args, is_url)
            
        finally:
            if temp_dir:
                print(f"Cleaning up temporary directory: {temp_dir.name}")
                temp_dir.cleanup()
    
    @staticmethod
    def _is_repository_url(source: str) -> bool:
        """Check if source is a repository URL."""
        return source.startswith(("http://", "https://", "git@"))
    
    async def _prepare_source(
        self, 
        source: str, 
        is_url: bool, 
        branch: Optional[str]
    ) -> tuple[str, Optional[object]]:
        """Prepare local source path and handle temporary directory if needed."""
        if is_url:
            temp_dir_obj = self.repo_handler.create_temp_directory()
            # On Python 3.8+, TemporaryDirectory has a .name attribute which is a string.
            # For older versions or type hinting, ensure it's treated as a string.
            local_path = str(temp_dir_obj.name)
            
            if not await self.repo_handler.clone_repo(source, local_path, branch):
                temp_dir_obj.cleanup() # Clean up if clone fails
                sys.exit(1)
                
            return local_path, temp_dir_obj
        else:
            # Resolve relative paths (like ".") to absolute paths
            local_path = str(Path(source).resolve())
            if not Path(local_path).exists():
                print(f"Error: Local source path does not exist: {local_path}", file=sys.stderr)
                sys.exit(1)
            return local_path, None
    
    async def _generate_exclude_patterns(
        self, 
        local_source_path: str, # Must be a directory
        args: argparse.Namespace
    ) -> set[str]:
        """Generate and combine exclude patterns."""
        patterns = set(args.exclude_pattern or []) # Ensure it's a list if None
        
        if not args.no_auto_exclude and self.gemini_client:
            auto_patterns = await self._generate_auto_exclude_patterns(local_source_path, args)
            patterns.update(auto_patterns)
        
        # Display patterns here if not dry run, or let dry run handle it
        if not args.dry_run:
            self._display_patterns(patterns)
        return patterns
    
    async def _generate_auto_exclude_patterns(
        self, 
        local_source_path: str, 
        args: argparse.Namespace
    ) -> set[str]:
        """Generate automatic exclude patterns using Gemini."""
        print(f"Analyzing directory structure: {local_source_path}")
        
        directory_tree = self.analyzer.create_directory_tree(local_source_path)
        
        if args.show_tree:
            print("\n--- Directory Tree ---")
            print(directory_tree)
            print("--- End Tree ---\n")
        
        if not self.gemini_client: # Should have been caught earlier, but defensive check
            print("Gemini client not available. Skipping automatic pattern generation.", file=sys.stderr)
            return set()

        print(f"Generating intelligent exclude patterns using Gemini ({self.config.gemini_model})...")
        
        if auto_patterns := await self.gemini_client.generate_patterns(directory_tree):
            print("\nAutomatically generated exclude patterns:")
            for pattern in sorted(auto_patterns):
                print(f"  - {pattern}")
            return auto_patterns
        else:
            print("\nFailed to generate or no automatic exclude patterns returned. Using manual patterns only if provided.")
            return set()
    
    @staticmethod
    def _display_patterns(patterns: set[str], title: str = "Final Exclude Patterns:") -> None:
        """Display final exclude patterns."""
        print(f"\n{title}")
        if patterns:
            for pattern in sorted(patterns):
                print(f"  - {pattern}")
        else:
            print("  (None)")
    
    @staticmethod
    def _generate_default_output_filename(original_source_str: str) -> str:
        """Generates a default output filename based on the original source string."""
        name_part = ""
        if original_source_str.startswith(("http://", "https://", "git@")):
            # Basic parsing for repo name from URL
            stripped_source = original_source_str
            if original_source_str.startswith("git@"):
                stripped_source = original_source_str.split(":", 1)[-1] # user/repo.git or path/to/repo.git
            
            name_part = Path(stripped_source).name # Gets repo.git or repo
            if name_part.endswith(".git"):
                name_part = name_part[:-4]
        else: 
            name_part = Path(original_source_str).name
        
        return f"digest-{name_part}.txt"

    async def _execute_gitingest(
        self, 
        local_source_path: str, # Absolute path to the content to be processed
        args: argparse.Namespace, # Contains original args.source
        is_url: bool 
    ) -> None:
        """Execute GitIngest with configured parameters."""
        
        # Resolve output file path to an absolute path based on the original CWD
        output_filename_default = self._generate_default_output_filename(args.source)
        output_file_path_str = args.output or output_filename_default
        output_file_absolute = str(Path(output_file_path_str).resolve())

        print(f"\nRunning GitIngest on: {local_source_path}")
        print(f"Output file: {output_file_absolute}")
        print(f"Max file size: {args.max_size} bytes")
        
        if args.include_pattern:
            print(f"Include patterns: {', '.join(sorted(args.include_pattern))}")
        
        original_cwd = os.getcwd()
        # Target path for gitingest (where it should effectively 'cd' to)
        gitingest_target_dir = local_source_path 
        # Argument for gitingest's source parameter
        gitingest_source_arg = local_source_path # Default: pass absolute path
        
        # Determine if we can and should chdir
        can_chdir = Path(gitingest_target_dir).is_dir()

        try:
            if can_chdir:
                print(f"Changing working directory to: {gitingest_target_dir} for GitIngest execution.")
                os.chdir(gitingest_target_dir)
                gitingest_source_arg = "." # Run with source as "." relative to the new CWD
            else:
                print(f"Source '{gitingest_target_dir}' is not a directory. Running GitIngest with absolute path.")


            # Determine branch argument for gitingest
            # If the source was a URL, smart-ingest already cloned the specified branch (or default).
            # gitingest's branch argument is for *its* cloning or for local repo branch switching.
            # If it was a local path, args.branch could specify a different branch for gitingest to process.
            gitingest_branch_arg = args.branch if not is_url and Path(gitingest_target_dir).is_dir() else None
            
            # Ensure exclude_patterns is a set, even if empty.
            # self.exclude_patterns is initialized as set(), so it should be fine.
            current_exclude_patterns = self.exclude_patterns if self.exclude_patterns else set()


            summary, _, _ = await ingest_async(
                source=gitingest_source_arg,
                max_file_size=args.max_size,
                include_patterns=set(args.include_pattern) if args.include_pattern else None,
                exclude_patterns=current_exclude_patterns if current_exclude_patterns else None,
                branch=gitingest_branch_arg,
                output=output_file_absolute # Use the absolute path for output
            )
            
            print(f"\nAnalysis complete! Output written to: {output_file_absolute}")
            print("\nSummary:")
            print(summary)
            
        except Exception as e:
            print(f"\nError running GitIngest: {str(e)}", file=sys.stderr)
            # Re-raise the exception so it's not silently swallowed,
            # allowing the main try/except or asyncio to handle it.
            raise
        finally:
            if can_chdir: # Only chdir back if we chdir'd in the first place
                print(f"Restoring original working directory: {original_cwd}")
                os.chdir(original_cwd)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Enhanced GitIngest with Gemini-powered automatic exclude pattern generation"
    )
    
    parser.add_argument("source", type=str, help="Source directory or repository URL (use '.' for current directory)")
    parser.add_argument("--output", "-o", help="Output file path (default: digest-<name>.txt)")
    parser.add_argument("--max-size", "-s", type=int, default=10*1024*1024, 
                        help="Maximum file size to process in bytes (default: 10MB)")
    parser.add_argument("--exclude-pattern", "-e", action="append", default=None, # Changed default to None
                        help="Additional patterns to exclude (can be specified multiple times)")
    parser.add_argument("--include-pattern", "-i", action="append", 
                        help="Patterns to include (overrides excludes, can be specified multiple times)")
    parser.add_argument("--branch", "-b", help="Branch to clone if source is a URL, or branch to process if source is a local git repo.")
    parser.add_argument("--api-key", help="Gemini API key (overrides GEMINI_API_KEY environment variable)")
    parser.add_argument("--gemini-model", help="Gemini model for pattern generation (overrides GEMINI_MODEL environment variable)")
    parser.add_argument("--no-auto-exclude", action="store_true", 
                        help="Disable automatic exclude pattern generation via Gemini")
    parser.add_argument("--max-depth", type=int, default=8, 
                        help="Maximum directory traversal depth for analysis tree (default: 8)")
    parser.add_argument("--dry-run", action="store_true", 
                        help="Generate and show exclude patterns without performing ingestion")
    parser.add_argument("--show-tree", action="store_true", 
                        help="Show the directory tree used for analysis")
    parser.add_argument("--retries", type=int, default=3, 
                        help="Number of Gemini API call retries (default: 3)")
    
    return parser.parse_args()


async def main():
    """Entry point."""
    args = parse_arguments()
    config = load_config(args) # Load config early
    
    app = SmartIngestApp(config)
    try:
        await app.run(args)
    except Exception as e:
        # Catch exceptions from app.run (like the re-raised one from _execute_gitingest)
        print(f"An error occurred during execution: {e}", file=sys.stderr)
        # Potentially exit with error code if needed, but avoid sys.exit in library-like code
        # For a CLI tool, sys.exit(1) might be appropriate here. For now, just print.
        # sys.exit(1) # Uncomment if strict CLI error exit is desired


if __name__ == "__main__":
    asyncio.run(main())