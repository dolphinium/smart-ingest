"""Directory structure analyzer for Smart Ingest."""

import os
from pathlib import Path
from typing import Union


class DirectoryAnalyzer:
    """Analyzes directory structure for Smart Ingest."""
    
    def __init__(self, max_depth: int = 8):
        self.max_depth = max_depth
    
    def create_directory_tree(
        self, 
        path: Union[str, Path], 
        depth: int = 0, 
        prefix: str = "", 
        is_last: bool = True, 
        is_root: bool = True
    ) -> str:
        """Create text representation of directory structure."""
        if depth > self.max_depth:
            return prefix + "└── [Max depth reached]\n"
        
        path = Path(path)
        if not path.exists():
            return prefix + f"└── [Path not found: {path.name}]\n"
        
        base_name = path.name
        if is_root:
            result = base_name + ("/" if path.is_dir() else "") + "\n"
            connector = ""
        else:
            connector = "└── " if is_last else "├── "
            result = prefix + connector + base_name + ("/" if path.is_dir() else "") + "\n"
        
        if path.is_dir():
            new_prefix = prefix + ("    " if is_last or is_root else "│   ")
            
            try:
                items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                result += new_prefix + "└── [Permission denied]\n"
                return result
            except OSError as e:
                result += new_prefix + f"└── [Error listing: {e}]\n"
                return result
            
            for i, item in enumerate(items):
                last = (i == len(items) - 1)
                result += self.create_directory_tree(
                    item,
                    depth + 1,
                    new_prefix,
                    last,
                    False
                )
        
        return result