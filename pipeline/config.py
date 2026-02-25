#!/usr/bin/env python3
"""
Central configuration for Literature Processing Pipeline
--------------------------------------------------------
This file manages all paths and settings for the project.
Updated for flattened data directory structure.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Get the correct project root
# config.py is in pdf_processing/scripts/, so we go up to Literature/
SCRIPTS_DIR = Path(__file__).parent
PDF_PROCESSING_DIR = SCRIPTS_DIR.parent
LITERATURE_ROOT = PDF_PROCESSING_DIR.parent

# Load environment variables from Literature/.env
load_dotenv(LITERATURE_ROOT / '.env')

# === PATH CONFIGURATION ===
# Updated for flattened structure - all data folders directly under data/
BASE_DIR = PDF_PROCESSING_DIR
DATA_DIR = BASE_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"

# ChatGPT folders (now directly under data/)
CHATGPT_CHUNKS_DIR = DATA_DIR / "chunked_jsons_concatenated"
CHATGPT_QA_DIR = DATA_DIR / "qa_jsons"

# Claude folders (now directly under data/)
CLAUDE_COMPLETE_DIR = DATA_DIR / "chunked_txts_concatenated"
CLAUDE_BY_AUTHOR_DIR = DATA_DIR / "chunked_txts_by_author"

# Legacy paths for compatibility (point to same locations)
CHATGPT_DIR = DATA_DIR  # For any scripts that might still reference this
CLAUDE_DIR = DATA_DIR   # For any scripts that might still reference this

# Metadata and state files
METADATA_EXCEL = BASE_DIR / "pdf_metadata.xlsx"
STATE_FILE = BASE_DIR / "processed.json"
META_FILE = BASE_DIR / "meta_index.json"

# === API CONFIGURATION ===
API_KEY = os.getenv('OPENAI_API_KEY')
TEXT_STORE_ID = os.getenv('TEXT_STORE_ID')
AUTHOR_QA_GEN_V2_ID = os.getenv('AUTHOR_QA_GEN_V2_ID')
AUTHOR_QA_QC_V2_ID = os.getenv('AUTHOR_QA_QC_V2_ID')

# === PROCESSING SETTINGS ===
# Chunking parameters
MAX_TOKENS = 600
OVERLAP_RATIO = 0.2
MIN_TOKENS = 80

# File patterns
PDF_PATTERN = "*.pdf"
JSON_PATTERN = "*.json"
TXT_PATTERN = "*.txt"

# === UTILITY FUNCTIONS ===
def ensure_directories():
    """Create all required directories if they don't exist"""
    dirs = [
        PDF_DIR,
        CHATGPT_CHUNKS_DIR,
        CHATGPT_QA_DIR,
        CLAUDE_COMPLETE_DIR,
        CLAUDE_BY_AUTHOR_DIR
    ]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)

def get_project_root():
    """Get the Literature root directory"""
    return LITERATURE_ROOT

def get_scripts_dir():
    """Get the scripts directory"""
    return SCRIPTS_DIR

# === VALIDATION ===
def validate_config():
    """Validate that all required paths and environment variables are set"""
    issues = []
    
    # Check directories
    if not BASE_DIR.exists():
        issues.append(f"Base directory not found: {BASE_DIR}")
    if not DATA_DIR.exists():
        issues.append(f"Data directory not found: {DATA_DIR}")
    
    # Check environment variables
    if not API_KEY:
        issues.append("OPENAI_API_KEY not set in .env")
    if not TEXT_STORE_ID:
        issues.append("TEXT_STORE_ID not set in .env")
    
    return issues

# === BACKWARDS COMPATIBILITY ===
# For scripts that might still reference the old structure
ROOT = LITERATURE_ROOT  # Some scripts use ROOT variable

if __name__ == "__main__":
    # Test configuration
    print("🔍 Configuration Check")
    print("=" * 60)
    print(f"Literature Root: {LITERATURE_ROOT}")
    print(f"PDF Processing Dir: {PDF_PROCESSING_DIR}")
    print(f"Scripts Directory: {SCRIPTS_DIR}")
    print(f"Data Directory: {DATA_DIR}")
    print()
    
    issues = validate_config()
    if issues:
        print("❌ Configuration Issues:")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("✅ Configuration valid!")
    
    print("\n📁 Directory Structure (Flattened):")
    for name, path in [
        ("PDFs", PDF_DIR),
        ("JSON Chunks", CHATGPT_CHUNKS_DIR),
        ("QA JSONs", CHATGPT_QA_DIR),
        ("Claude Complete", CLAUDE_COMPLETE_DIR),
        ("Claude by Author", CLAUDE_BY_AUTHOR_DIR),
        ("Metadata Excel", METADATA_EXCEL),
    ]:
        exists = "✅" if path.exists() else "❌"
        try:
            rel_path = path.relative_to(LITERATURE_ROOT)
        except:
            rel_path = path
        print(f"   {exists} {name}: {rel_path}")
    
    print("\n🔑 Environment Variables:")
    env_vars = [
        ("OPENAI_API_KEY", API_KEY, True),
        ("TEXT_STORE_ID", TEXT_STORE_ID, False),
        ("AUTHOR_QA_GEN_V2_ID", AUTHOR_QA_GEN_V2_ID, False),
        ("AUTHOR_QA_QC_V2_ID", AUTHOR_QA_QC_V2_ID, False),
    ]
    
    for name, value, hide in env_vars:
        if value:
            if hide:
                display = f"{value[:8]}..." if len(value) > 8 else value
            else:
                display = value
            print(f"   ✅ {name}: {display}")
        else:
            print(f"   ❌ {name}: Not set")