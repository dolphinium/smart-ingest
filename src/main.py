"""Enhanced GitIngest with Gemini-powered automatic exclude pattern generation."""

import argparse
import asyncio
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
    
    async def run(self, args: argparse.Namespace) -> None:
        """Main execution flow."""
        # Determine if source is URL or local path
        source = args.source
        is_url = self._is_repository_url(source)
        
        # Prepare local source path
        local_source_path, temp_dir = await self._prepare_source(source, is_url, args.branch)
        
        try:
            # Generate exclude patterns
            self.exclude_patterns = await self._generate_exclude_patterns(
                local_source_path, args, is_url
            )
            
            # Dry run or full execution
            if args.dry_run:
                print("\nDry run requested. Exiting without performing ingestion.")
                return
            
            # Execute GitIngest
            await self._execute_gitingest(local_source_path, args, is_url)
            
        finally:
            # Cleanup
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
            temp_dir = self.repo_handler.create_temp_directory()
            local_path = temp_dir.name
            
            if not await self.repo_handler.clone_repo(source, local_path, branch):
                sys.exit(1)
                
            return local_path, temp_dir
        else:
            local_path = str(Path(source).resolve())
            if not Path(local_path).exists():
                print(f"Error: Local source path does not exist: {local_path}", file=sys.stderr)
                sys.exit(1)
            return local_path, None
    
    async def _generate_exclude_patterns(
        self, 
        local_source_path: str, 
        args: argparse.Namespace,
        is_url: bool
    ) -> set[str]:
        """Generate and combine exclude patterns."""
        patterns = set(args.exclude_pattern)
        
        if not args.no_auto_exclude and self.gemini_client and Path(local_source_path).is_dir():
            auto_patterns = await self._generate_auto_exclude_patterns(local_source_path, args)
            patterns.update(auto_patterns)
        
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
        
        print(f"Generating intelligent exclude patterns using Gemini ({self.config.gemini_model})...")
        
        if auto_patterns := await self.gemini_client.generate_patterns(directory_tree):
            print("\nAutomatically generated exclude patterns:")
            for pattern in sorted(auto_patterns):
                print(f"  - {pattern}")
            return auto_patterns
        else:
            print("\nFailed to generate automatic exclude patterns. Using manual patterns only.")
            return set()
    
    @staticmethod
    def _display_patterns(patterns: set[str]) -> None:
        """Display final exclude patterns."""
        print("\nFinal Exclude Patterns:")
        if patterns:
            for pattern in sorted(patterns):
                print(f"  - {pattern}")
        else:
            print("  (None)")
    
    async def _execute_gitingest(
        self, 
        local_source_path: str, 
        args: argparse.Namespace,
        is_url: bool
    ) -> None:
        """Execute GitIngest with configured parameters."""
        output_file = args.output or self._get_default_output(local_source_path, is_url)
        
        print(f"\nRunning GitIngest on: {local_source_path}")
        print(f"Output file: {output_file}")
        print(f"Max file size: {args.max_size} bytes")
        
        if args.include_pattern:
            print(f"Include patterns: {', '.join(sorted(args.include_pattern))}")
        
        try:
            summary, _, _ = await ingest_async(
                source=local_source_path,
                max_file_size=args.max_size,
                include_patterns=set(args.include_pattern) if args.include_pattern else None,
                exclude_patterns=self.exclude_patterns if self.exclude_patterns else None,
                branch=args.branch if is_url else None,
                output=output_file
            )
            
            print(f"\nAnalysis complete! Output written to: {output_file}")
            print("\nSummary:")
            print(summary)
            
        except Exception as e:
            print(f"\nError running GitIngest: {str(e)}", file=sys.stderr)
            sys.exit(1)
    
    @staticmethod
    def _get_default_output(source_path: str, is_url: bool) -> str:
        """Get default output filename."""
        if is_url:
            repo_name = source_path.split('/')[-1].replace('.git', '')
            return f"digest-{repo_name}.txt"
        else:
            dir_name = Path(source_path).name
            return f"digest-{dir_name}.txt"


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Enhanced GitIngest with Gemini-powered automatic exclude pattern generation"
    )
    
    parser.add_argument("source", type=str, help="Source directory or repository URL")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--max-size", "-s", type=int, default=10*1024*1024, 
                        help="Maximum file size to process in bytes (default: 10MB)")
    parser.add_argument("--exclude-pattern", "-e", action="append", default=[], 
                        help="Additional patterns to exclude (can be specified multiple times)")
    parser.add_argument("--include-pattern", "-i", action="append", 
                        help="Patterns to include (overrides excludes, can be specified multiple times)")
    parser.add_argument("--branch", "-b", help="Branch to clone and ingest if source is a URL")
    parser.add_argument("--api-key", help="Gemini API key (overrides GEMINI_API_KEY environment variable)")
    parser.add_argument("--gemini-model", help="Gemini model for pattern generation")
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
    
    # Parse arguments and load config
    args = parse_arguments()
    config = load_config(args)
    
    # Create and run application
    app = SmartIngestApp(config)
    await app.run(args)


if __name__ == "__main__":
    asyncio.run(main())