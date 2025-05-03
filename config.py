import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration for Smart Ingest."""
    
    api_key: Optional[str]
    gemini_model: str
    max_depth: int
    retries: int


def load_config(args) -> Config:
    """Load configuration from environment and arguments."""
    load_dotenv()
    
    api_key = args.api_key or os.getenv("GEMINI_API_KEY")
    default_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    
    if not args.no_auto_exclude and not api_key:
        print("Warning: --no-auto-exclude not set, but no Gemini API key found. Disabling automatic excludes.")
    
    return Config(
        api_key=api_key,
        gemini_model=args.gemini_model or default_model,
        max_depth=args.max_depth,
        retries=args.retries
    )