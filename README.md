# Smart Ingest

[![Python Version](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

**Smart Ingest** is a tool designed to intelligently prepare code repositories or local directories for analysis by Large Language Models (LLMs). It enhances the functionality of the `gitingest` library by incorporating Google Gemini to automatically generate relevant `.gitignore`-style exclude patterns. This helps ensure that only meaningful code is included in the output digest, filtering out dependencies, build artifacts, configuration files, and other noise.

## Core Features

*   **Intelligent Exclusions:** Leverages Google Gemini (via `google-generativeai`) to analyze the directory structure and suggest contextually relevant files and directories to exclude (e.g., `node_modules`, `venv`, `__pycache__`, build outputs, IDE configs).
*   **Source Flexibility:** Ingests code from both local directories and remote Git repositories (HTTP/HTTPS/SSH).
*   **Customizable Filtering:** Allows users to provide additional custom include and exclude patterns alongside the automatically generated ones.
*   **`gitingest` Integration:** Builds upon the `gitingest` library for the core code collection and formatting process.
*   **Configuration:** Configurable via environment variables (`.env` file) and command-line arguments (API keys, Gemini model, analysis depth, retries).
*   **Dry Run Mode:** Preview the directory structure analysis and the final set of exclude patterns without actually performing the ingestion.
*   **Directory Tree View:** Option to display the analyzed directory tree that is sent to Gemini for pattern generation.

## How it Works

1.  **Source Preparation:** Clones the repository into a temporary directory if a URL is provided, or resolves the path if a local directory is given.
2.  **Directory Analysis (Optional):** If automatic exclusion is enabled (default), it analyzes the directory structure up to a specified depth (`--max-depth`).
3.  **Gemini Pattern Generation (Optional):** Sends the analyzed directory tree structure to the configured Google Gemini model. A specialized prompt guides Gemini to identify common files/directories (present *only* in the provided structure) that should typically be excluded for LLM analysis and return them as `.gitignore`-style patterns.
4.  **Pattern Consolidation:** Combines automatically generated patterns (if any) with user-provided exclude patterns (`--exclude-pattern`). User-provided include patterns (`--include-pattern`) take precedence.
5.  **Ingestion:** Executes `gitingest` with the source path and the final consolidated set of include/exclude patterns.
6.  **Output:** Writes the collected code content into a single output file (default: `digest-<repo_name>.txt` or `digest-<dir_name>.txt`).
7.  **Cleanup:** Removes the temporary directory if a repository was cloned.

## Requirements

*   Python 3.7+
*   Git (must be installed and in your system's PATH if cloning repositories)
*   Dependencies listed in `requirements.txt`:
    *   `gitingest`
    *   `google-generativeai`
    *   `python-dotenv`
    *   `pydantic`

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/dolphinium/smart-ingest
    cd smart_ingest
    ```
2.  **Create and activate a virtual environment (I recommended conda):**
    ```bash
    conda create -n smart-ingest python=3.10 -y 
    conda activate smart-ingest
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

Smart Ingest uses environment variables for configuration, primarily for the Gemini API.

1.  **Copy the example environment file:**
    ```bash
    cp .env.example .env
    ```
2.  **Edit the `.env` file:**
    ```dotenv
    # .env
    GEMINI_API_KEY=your_google_generative_ai_api_key_here # REQUIRED for automatic exclusion
    GEMINI_MODEL=gemini-2.0-flash # Optional: Defaults to 'gemini-2.0-flash' if not set
    ```
    *   Replace `your_google_generative_ai_api_key_here` with your actual API key obtained from [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   You can optionally change the `GEMINI_MODEL` to another compatible model.

*Note: The `--api-key` and `--gemini-model` command-line arguments override these environment variables.*

**Important:** If you don't provide a `GEMINI_API_KEY` (either via `.env` or `--api-key`), the automatic exclude pattern generation feature will be disabled, even if `--no-auto-exclude` is not set.

## Usage

Run the script from the command line using `python main.py`.

```bash
python main.py <source> [options]
```

**Examples:**

1.  **Ingest a local directory with automatic exclusions:**
    ```bash
    python main.py /path/to/your/project
    ```
    *(Requires `GEMINI_API_KEY` to be set in `.env` or passed via `--api-key`)*

2.  **Ingest a remote Git repository (main branch) with automatic exclusions:**
    ```bash
    python main.py https://github.com/user/repo.git
    ```
    *(Requires `GEMINI_API_KEY`)*

3.  **Ingest a specific branch of a remote repository:**
    ```bash
    python main.py https://github.com/user/repo.git --branch develop
    ```

4.  **Ingest with custom exclude patterns (disables automatic generation):**
    ```bash
    python main.py /path/to/project --no-auto-exclude -e "docs/" -e "*.log"
    ```

5.  **Ingest with automatic exclusions *and* additional custom patterns:**
    ```bash
    python main.py /path/to/project -e "config.yaml" -e "temp_files/"
    ```
    *(Gemini will suggest patterns, and `config.yaml`, `temp_files/` will also be excluded)*

6.  **Ingest only specific file types using include patterns:**
    ```bash
    python main.py /path/to/project --no-auto-exclude -i "*.py" -i "*.js"
    ```
    *(Note: Include patterns override excludes. Use with caution, may include unwanted files if automatic exclusion is off)*

7.  **Specify a custom output file:**
    ```bash
    python main.py /path/to/project -o my_project_digest.txt
    ```

8.  **Dry run: See the generated exclude patterns without ingesting:**
    ```bash
    python main.py /path/to/project --dry-run
    ```

9.  **Dry run and show the directory tree sent to Gemini:**
    ```bash
    python main.py /path/to/project --dry-run --show-tree
    ```

## Command-Line Options

```
usage: main.py [-h] [--output OUTPUT] [--max-size MAX_SIZE] [--exclude-pattern EXCLUDE_PATTERN] [--include-pattern INCLUDE_PATTERN] [--branch BRANCH] [--api-key API_KEY]
               [--gemini-model GEMINI_MODEL] [--no-auto-exclude] [--max-depth MAX_DEPTH] [--dry-run] [--show-tree] [--retries RETRIES]
               source

Enhanced GitIngest with Gemini-powered automatic exclude pattern generation

positional arguments:
  source                Source directory or repository URL

options:
  -h, --help            show this help message and exit
  --output OUTPUT, -o OUTPUT
                        Output file path (default: digest-<name>.txt)
  --max-size MAX_SIZE, -s MAX_SIZE
                        Maximum file size to process in bytes (default: 10MB)
  --exclude-pattern EXCLUDE_PATTERN, -e EXCLUDE_PATTERN
                        Additional patterns to exclude (can be specified multiple times)
  --include-pattern INCLUDE_PATTERN, -i INCLUDE_PATTERN
                        Patterns to include (overrides excludes, can be specified multiple times)
  --branch BRANCH, -b BRANCH
                        Branch to clone and ingest if source is a URL
  --api-key API_KEY     Gemini API key (overrides GEMINI_API_KEY environment variable)
  --gemini-model GEMINI_MODEL
                        Gemini model for pattern generation (overrides GEMINI_MODEL environment variable)
  --no-auto-exclude     Disable automatic exclude pattern generation via Gemini
  --max-depth MAX_DEPTH
                        Maximum directory traversal depth for analysis tree (default: 8)
  --dry-run             Generate and show exclude patterns without performing ingestion
  --show-tree           Show the directory tree used for analysis
  --retries RETRIES     Number of Gemini API call retries (default: 3)
```

