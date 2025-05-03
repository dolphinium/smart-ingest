"""Gemini API client for generating exclude patterns."""

import asyncio
import re
import sys
from typing import Optional

import google.generativeai as genai
from pydantic import BaseModel, Field, field_validator


class ExcludePatterns(BaseModel):
    """Model for exclude patterns."""
    
    patterns: list[str] = Field(description="List of exclude patterns as strings")
    
    @field_validator('patterns', mode='before')
    @classmethod
    def parse_and_clean_patterns(cls, v):
        """Validate and clean patterns from string or list."""
        raw_patterns = []
        
        if isinstance(v, str):
            # Remove code block markers and split comma-separated string
            cleaned_str = re.sub(r'(^```[a-zA-Z]*\s*|\s*```$)', '', v, flags=re.MULTILINE).strip()
            raw_patterns = [p.strip() for p in cleaned_str.split(',') if p.strip()]
        elif isinstance(v, list):
            raw_patterns = [str(p).strip() for p in v if str(p).strip()]
        else:
            raise ValueError("Patterns must be a string or list")
        
        # Clean each pattern
        valid_patterns = []
        for pattern in raw_patterns:
            cleaned_pattern = pattern.strip('\'"` ')
            if cleaned_pattern:
                valid_patterns.append(cleaned_pattern)
        
        return valid_patterns


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


class GeminiExcludePatternGenerator:
    """Gemini API client for generating exclude patterns."""
    
    def __init__(self, api_key: str, model_name: str, retries: int = 3):
        self.api_key = api_key
        self.model_name = model_name
        self.retries = retries
        self.model = None
        
    async def generate_patterns(self, directory_structure: str) -> Optional[set[str]]:
        """Generate exclude patterns using Gemini API."""
        if not self._configure_api():
            return None
        
        prompt = self._create_prompt(directory_structure)
        
        for attempt in range(self.retries):
            print(f"Calling Gemini API (Attempt {attempt + 1}/{self.retries})...")
            
            try:
                response = await self._call_api(prompt)
                if response:
                    return response
                
            except genai.types.generation_types.BlockedPromptException as e:
                print(f"Attempt {attempt + 1}: Gemini API call failed due to blocked prompt: {e}", file=sys.stderr)
                break
            except Exception as e:
                print(f"Attempt {attempt + 1}: Error calling Gemini API: {str(e)}", file=sys.stderr)
                if attempt < self.retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        print("Failed to generate valid exclude patterns from Gemini API after all attempts.", file=sys.stderr)
        return None
    
    def _configure_api(self) -> bool:
        """Configure Gemini API with key."""
        try:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=SYSTEM_PROMPT,
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 1024,
                }
            )
            return True
        except Exception as e:
            print(f"Error configuring Gemini API: {e}", file=sys.stderr)
            return False
    
    @staticmethod
    def _create_prompt(directory_structure: str) -> str:
        """Create prompt for Gemini API."""
        return (
            f"Analyze the following directory structure and generate a single comma-separated "
            f"line of exclude patterns based *only* on the items present. Follow the exclusion "
            f"guidelines strictly.\n\n"
            f"Directory structure:\n```\n{directory_structure}\n```\n\n"
            f"Exclude patterns:"
        )
    
    async def _call_api(self, prompt: str) -> Optional[set[str]]:
        """Call Gemini API and parse response."""
        response = self.model.generate_content(prompt)
        raw_text = response.text.strip()
        
        print(f"Gemini Raw Response:\n---\n{raw_text}\n---")
        
        try:
            parsed_patterns = ExcludePatterns(patterns=raw_text)
            if parsed_patterns.patterns:
                return set(parsed_patterns.patterns)
            else:
                print("Warning: Gemini returned an empty pattern list.")
                return None
        except Exception as e:
            print(f"Failed to parse/validate response: {e}")
            print(f"Raw response was: {raw_text}")
            return None