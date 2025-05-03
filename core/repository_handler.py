"""Repository handling for Smart Ingest."""

import asyncio
import subprocess
import tempfile
from typing import Optional, Tuple


class RepositoryHandler:
    """Handles repository cloning operations."""
    
    @staticmethod
    async def clone_repo(repo_url: str, target_dir: str, branch: Optional[str] = None) -> bool:
        """Clone a Git repository."""
        print(f"Cloning '{repo_url}'" + (f" (branch: {branch})" if branch else "") + f" into '{target_dir}'...")
        
        cmd = ["git", "clone", "--depth", "1", "--quiet"]
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
    
    @staticmethod
    def create_temp_directory() -> tempfile.TemporaryDirectory:
        """Create a temporary directory for repository cloning."""
        return tempfile.TemporaryDirectory(prefix="smart_ingest_clone_")