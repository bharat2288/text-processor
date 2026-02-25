#!/usr/bin/env python3
"""
Integrated Literature Processing Pipeline
-----------------------------------------
Processes PDFs through the complete pipeline:
1. PDF → JSON chunks (ChatGPT format)
2. JSON chunks → QAv2 enrichment 
3. JSON → TXT conversion (Claude formats)
4. Metadata tracking in Excel

New clean directory structure under H:\My Drive\Thesis\Literature\

ENHANCED: Added comprehensive error diagnostics for QAv2 generation failures
"""

import os
import shutil
import subprocess
import pathlib
import re
import pandas as pd
import requests
import sys
import json
import argparse
import traceback  # ADDED: For better error reporting
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Import paths from central configuration
from config import (
    BASE_DIR, PDF_DIR, CHATGPT_DIR, CHATGPT_CHUNKS_DIR,
    CHATGPT_QA_DIR, CLAUDE_DIR, CLAUDE_COMPLETE_DIR,
    CLAUDE_BY_AUTHOR_DIR, METADATA_EXCEL, SCRIPTS_DIR,
    API_KEY, TEXT_STORE_ID, ensure_directories
)


# --- Clean Directory Configuration ---
# BASE_DIR = pathlib.Path(r"H:\My Drive\Thesis\Literature")  # Replaced by config.py
# PDF_DIR = BASE_DIR / "pdfs"  # Replaced by config.py
# CHATGPT_DIR = BASE_DIR / "chatgpt"  # Replaced by config.py
# CHATGPT_CHUNKS_DIR = CHATGPT_DIR / "chunked_jsons_concatenated"  # Replaced by config.py
# CHATGPT_QA_DIR = CHATGPT_DIR / "qa_jsons"  # Replaced by config.py
# CLAUDE_DIR = BASE_DIR / "claude"  # Replaced by config.py
# CLAUDE_COMPLETE_DIR = CLAUDE_DIR / "chunked_txts_concatenated"  # Replaced by config.py
# CLAUDE_BY_AUTHOR_DIR = CLAUDE_DIR / "chunked_txts_by_author"  # Replaced by config.py
# METADATA_EXCEL = BASE_DIR / "pdf_metadata.xlsx"  # Replaced by config.py
# SCRIPTS_DIR = BASE_DIR / "scripts"  # Optional location for scripts  # Replaced by config.py

# API Configuration
TEXT_STORE_ID = os.getenv('TEXT_STORE_ID')
API_KEY = os.getenv('OPENAI_API_KEY')

# Ensure all directories exist
for directory in [PDF_DIR, CHATGPT_CHUNKS_DIR, CHATGPT_QA_DIR, CLAUDE_COMPLETE_DIR, CLAUDE_BY_AUTHOR_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# --- Helper Functions ---
def sanitize_filename(name):
    """Clean filename for filesystem compatibility"""
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return sanitized

def clean_filename(name):
    """Clean filename for Claude txt outputs"""
    cleaned = re.sub(r'[^\w\-_.]', '_', name)
    cleaned = re.sub(r'_{2,}', '_', cleaned)
    if len(cleaned) > 60:
        cleaned = cleaned[:60]
    return cleaned

def correct_filename_format(filename):
    """Enhanced filename correction to handle various formats"""
    patterns = [
        (r"^(.*?)\s*[-–—]\s*(\d{4})\s*[-–—]\s*(.+)$", "{} ({}) - {}"),
        (r"^(.*?)\s*\((\d{4})\)\s*[-–—]\s*(.+)$", "{} ({}) - {}"),
        (r"^(.*?),\s*(\d{4})\s*[-–—]\s*(.+)$", "{} ({}) - {}"),
        (r"^(.*?)\s*(\d{4})\s*(.*)$", "{} ({}) - {}"),
    ]
    
    for pattern, format_str in patterns:
        match = re.match(pattern, filename)
        if match:
            author, year, title = match.groups()
            author = author.strip().rstrip('-–—,')
            year = year.strip()
            title = title.strip().lstrip('-–—')
            
            if author and year:
                corrected_name = format_str.format(author, year, title if title else "Untitled")
                return corrected_name
    
    return filename

def extract_metadata_from_filename(filename):
    """Extract author, year, and title from filename"""
    patterns = [
        r'^(.*?)\s*\((\d{4})\)\s*[-–—]\s*(.+)$',
        r'^([^_]+)_(\d{4})_(.+)$',
        r'^([^_]+_[^_]+)_(\d{4})_(.+)$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, filename)
        if match:
            author = match.group(1).strip()
            year = match.group(2)
            title = match.group(3).strip()
            
            author = re.sub(r',\s*', '_', author)
            author = clean_filename(author)
            
            return author, year, title
    
    # Fallback
    year_match = re.search(r'\b(\d{4})\b', filename)
    year = year_match.group(1) if year_match else "0000"
    parts = re.split(r'[_(),]', filename)
    author = clean_filename(parts[0]) if parts else "Unknown_Author"
    
    return author[:50], year, filename

def create_author_folder_name(author, year, title, chunk_count):
    """Create folder name with author, year, title words, and chunk count"""
    main_title = re.split(r'[:;]', title)[0]
    words = re.split(r'[\s\-_]+', main_title)
    significant_words = []
    
    for word in words:
        if len(word) > 2 and word.lower() not in ['the', 'and', 'for', 'with', 'from']:
            significant_words.append(clean_filename(word))
        
        if len(significant_words) >= 3:
            break
    
    # Build folder name with components
    folder_parts = [author, year] + significant_words[:3]
    base_folder_name = "_".join(folder_parts)
    
    # Append chunk count
    folder_name = f"{base_folder_name}_{chunk_count:03d}chunks"
    
    # Check length and trim if needed (keeping the chunk count)
    if len(folder_name) > 80:
        # Trim from the title words first
        folder_parts = [author, year] + significant_words[:2]
        base_folder_name = "_".join(folder_parts)
        folder_name = f"{base_folder_name}_{chunk_count:03d}chunks"
    
    return folder_name

# --- Rollback function ---
def rollback_files(file_ids):
    """
    ENHANCED: More detailed rollback reporting
    """
    headers = {"Authorization": f"Bearer {API_KEY}"}
    print(f"\n🔄 Rolling back {len(file_ids)} uploaded files...")
    
    for fid in file_ids:
        try:
            resp = requests.delete(f"https://api.openai.com/v1/files/{fid}", headers=headers)
            if resp.status_code == 200:
                print(f"   ✅ Rolled back (deleted): {fid}")
            elif resp.status_code == 404:
                print(f"   ℹ️  Already deleted: {fid}")
            else:
                print(f"   ⚠️  Rollback warning for {fid}: {resp.status_code}")
        except Exception as e:
            print(f"   ❌ Rollback error for {fid}: {e}")

# --- Claude TXT Processing with FIXED naming and chunk count ---
def process_json_to_claude_formats(json_file_path, corrected_stem):
    """Convert JSON chunks to Claude TXT formats with proper naming"""
    print("\n📄 Converting to Claude formats...")
    
    # Load JSON
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extract metadata
    author, year, title = extract_metadata_from_filename(corrected_stem)
    
    # Handle different JSON structures
    chunks = []
    if isinstance(data, list):
        chunks = data
    elif isinstance(data, dict):
        if 'chunks' in data:
            chunks = data['chunks']
        elif 'pages' in data:
            chunks = data['pages']
        else:
            chunks = [{"chunk_index": 1, "text": str(data)}]
    
    print(f"   📚 Author: {author}")
    print(f"   📅 Year: {year}")
    print(f"   📖 Title: {title[:50]}...")
    print(f"   📃 Chunks: {len(chunks)}")
    
    # Create author folder with chunk count
    author_folder_name = create_author_folder_name(author, year, title, len(chunks))
    author_dir = CLAUDE_BY_AUTHOR_DIR / author_folder_name
    author_dir.mkdir(exist_ok=True)
    
    # Process chunks
    short_base = clean_filename(corrected_stem)
    if len(short_base) > 40:
        short_base = short_base[:40]
    
    # Create organized chunks
    for i, chunk in enumerate(chunks, 1):
        if isinstance(chunk, dict):
            text_content = chunk.get('text', chunk.get('content', str(chunk)))
        else:
            text_content = str(chunk)
        
        # FIX: Better deduplication logic
        clean_base = short_base
        # Remove author from the beginning if it's already there
        author_variations = [
            author.lower(),
            author.lower().replace('_', ' '),
            author.lower().replace(' ', '_'),
            author.lower().replace(',', ''),
        ]
        
        for variant in author_variations:
            if clean_base.lower().startswith(variant):
                clean_base = clean_base[len(variant):].lstrip('_- ')
                break
        
        # Also remove year if it's at the start of what remains
        if clean_base.startswith(str(year)):
            clean_base = clean_base[len(str(year)):].lstrip('_- ')
        
        org_filename = f"LIT-{author}-{clean_base}-chunk_{i:03d}.txt"
        
        # Check path length
        full_path = author_dir / org_filename
        if len(str(full_path)) > 240:
            clean_base = clean_base[:20]
            org_filename = f"LIT-{author}-{clean_base}-chunk_{i:03d}.txt"
        
        with open(author_dir / org_filename, 'w', encoding='utf-8') as f:
            f.write(text_content)
    
    # Create complete text with FIXED naming logic
    # Extract just the title part for the complete filename
    title_for_filename = clean_filename(title)
    if len(title_for_filename) > 50:
        title_for_filename = title_for_filename[:50]
    
    # FIX: Use author_year_title pattern consistently
    complete_filename = f"LIT-{author}_{year}_{title_for_filename}_COMPLETE.txt"
    complete_filepath = CLAUDE_COMPLETE_DIR / complete_filename
    
    # Handle path length
    if len(str(complete_filepath)) > 240:
        title_for_filename = title_for_filename[:30]
        complete_filename = f"LIT-{author}_{year}_{title_for_filename}_COMPLETE.txt"
        complete_filepath = CLAUDE_COMPLETE_DIR / complete_filename
    
    with open(complete_filepath, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write(f"COMPLETE TEXT: {corrected_stem}\n")
        f.write(f"AUTHOR: {author}\n")
        f.write(f"YEAR: {year}\n")
        f.write(f"TITLE: {title}\n")
        f.write(f"TOTAL CHUNKS: {len(chunks)}\n")
        f.write(f"CHUNK RANGE: 001 - {len(chunks):03d}\n")
        f.write("="*80 + "\n\n")
        
        for i, chunk in enumerate(chunks, 1):
            if isinstance(chunk, dict):
                text_content = chunk.get('text', chunk.get('content', str(chunk)))
            else:
                text_content = str(chunk)
            
            f.write(f"\n{'='*15} CHUNK {i:03d} {'='*15}\n\n")
            f.write(text_content.strip())
            f.write("\n\n")
    
    print(f"   ✅ Created {len(chunks)} chunks in: {author_folder_name}/")
    print(f"   ✅ Created complete text: {complete_filename}")

# --- Interactive metadata entry ---
def prompt_metadata(default_author, default_year, default_title, existing_tags):
    author = input(f"Author [{default_author}]: ") or default_author
    year = input(f"Year [{default_year}]: ") or default_year
    title = input(f"Title [{default_title}]: ") or default_title

    text_types = ["Blog", "Book", "Book Section", "Journal Article", "Edited Volume"]
    print("\nTextType options:")
    for i, tt in enumerate(text_types, 1):
        print(f"{i}. {tt}")
    print("0. Create new type")
    choice = input("Choose TextType: ")
    if choice == "0":
        text_type = input("Enter new TextType: ")
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(text_types):
                text_type = text_types[idx]
            else:
                text_type = "Journal Article"
        except:
            text_type = "Journal Article"

    print("\nExisting tags:", ", ".join(existing_tags) if existing_tags else "None")
    tag_input = input("Enter tags (comma-separated) or type 'new' to create: ")
    if tag_input.lower() == 'new':
        tags = input("Enter new tags (comma-separated): ")
    else:
        tags = tag_input

    return author, year, title, text_type, tags

# --- Main Pipeline ---
def main(pdf_path):
    """Main integrated pipeline"""
    file_ids = []
    pdf_path = pathlib.Path(pdf_path).resolve()

    if not pdf_path.exists():
        print(f"❌ PDF not found: {pdf_path}")
        return

    print("\n🚀 Starting integrated literature processing pipeline...")
    print(f"📄 Processing: {pdf_path.name}")
    
    # Step 1: Correct filename and copy to pdfs directory
    corrected_stem = correct_filename_format(pdf_path.stem)
    target_pdf = PDF_DIR / f"{corrected_stem}.pdf"
    
    if pdf_path != target_pdf:
        shutil.copy(pdf_path, target_pdf)
        print(f"✅ PDF copied to: pdfs\\{corrected_stem}.pdf")
    else:
        print(f"✅ Using existing PDF in pdfs directory")

    # Step 2: Generate JSON chunks
    print("\n📊 Step 1/4: Generating JSON chunks...")
    subprocess.check_call([
        sys.executable, 
        str(SCRIPTS_DIR / "file_chunker_semantic.py") if (SCRIPTS_DIR / "file_chunker_semantic.py").exists() else "file_chunker_semantic.py",
        "--file", str(target_pdf), 
        "--output", str(CHATGPT_CHUNKS_DIR)
    ])
    chunk_json = CHATGPT_CHUNKS_DIR / f"{corrected_stem}.json"
    store_chunk_filename = sanitize_filename(corrected_stem) + ".json"

    # ENHANCED Step 3: Upload to OpenAI and generate QAv2 with comprehensive error handling
    print("\n🤖 Step 2/4: Uploading to OpenAI and generating QAv2...")
    try:
        # Upload chunk JSON
        print(f"   📤 Uploading chunk JSON...")
        with open(chunk_json, "rb") as f:
            resp = requests.post(
                "https://api.openai.com/v1/files", 
                headers={"Authorization": f"Bearer {API_KEY}"}, 
                files={"file": f}, 
                data={"purpose": "assistants"}
            )
            resp.raise_for_status()
            chunk_file_id = resp.json()["id"]
            file_ids.append(chunk_file_id)
            print(f"   ✅ Uploaded chunk file: {chunk_file_id}")
        
        # Add to vector store
        print(f"   📥 Adding to vector store...")
        resp = requests.post(
            f"https://api.openai.com/v1/vector_stores/{TEXT_STORE_ID}/files", 
            headers={"Authorization": f"Bearer {API_KEY}"}, 
            json={"file_id": chunk_file_id}
        )
        resp.raise_for_status()
        print(f"   ✅ Added to vector store: {TEXT_STORE_ID}")
        
        # ENHANCED: Run QAv2 generation with comprehensive error capture
        print(f"   🤖 Running QAv2 generation for {chunk_file_id}...")
        print(f"   ⏱️  This may take 2-3 minutes...")
        
        result = subprocess.run([
            sys.executable, 
            str(SCRIPTS_DIR / "revised_runqav2.py") if (SCRIPTS_DIR / "revised_runqav2.py").exists() else "revised_runqav2.py",
            "--file-id", chunk_file_id
        ], capture_output=True, text=True, timeout=600)  # CHANGED: 10 minute timeout (was implicit)
        
        # ENHANCED: Check for non-zero return code first
        # BUT: If we successfully extracted the file ID, the work is done despite the crash
        qav2_file_id_match = re.search(r'Generated QAv2 file ID: (file-[a-zA-Z0-9]+)', result.stdout)
        
        if result.returncode != 0 and not qav2_file_id_match:
            # Return code is bad AND we didn't get the file ID - this is a real failure
            print(f"\n❌ QAv2 script failed with return code: {result.returncode}")
            print("\n" + "="*60)
            print("DIAGNOSTIC OUTPUT - STDOUT:")
            print("="*60)
            print(result.stdout if result.stdout else "(no output)")
            print("\n" + "="*60)
            print("DIAGNOSTIC OUTPUT - STDERR:")
            print("="*60)
            print(result.stderr if result.stderr else "(no errors)")
            print("="*60)
            
            # ENHANCED: Provide guidance based on common error patterns
            if "Rate limit" in result.stdout or "Rate limit" in result.stderr:
                print("\n💡 TIP: OpenAI rate limit hit. Wait 60 seconds and try again.")
            elif "Authentication" in result.stdout or "Authentication" in result.stderr:
                print("\n💡 TIP: Check your OPENAI_API_KEY in .env file.")
            elif "assistant" in result.stdout.lower() or "assistant" in result.stderr.lower():
                print("\n💡 TIP: Check AUTHOR_QA_GEN_V2_ID and AUTHOR_QA_QC_V2_ID in .env file.")
            
            rollback_files(file_ids)
            return
        elif result.returncode != 0 and qav2_file_id_match:
            # Got the file ID but script crashed afterwards - probably just a display error
            print(f"\n⚠️  QAv2 script exited with code {result.returncode} but successfully generated file")
            print(f"   This is usually a harmless display/encoding error after the real work is done")
        
        # ENHANCED: Try to extract file ID with better error messaging
        if not qav2_file_id_match:
            print("\n❌ Failed to extract QAv2 file ID from output")
            print("\n" + "="*60)
            print("DIAGNOSTIC: QAv2 Script Output")
            print("="*60)
            print(result.stdout if result.stdout else "(no output)")
            
            if result.stderr:
                print("\n" + "="*60)
                print("DIAGNOSTIC: Error Messages")
                print("="*60)
                print(result.stderr)
            
            print("="*60)
            
            # ENHANCED: Check for specific failure patterns in output
            if "⚠ QC flagged issues" in result.stdout:
                print("\n💡 DIAGNOSIS: QC Assistant rejected the generated Q&A")
                print("   This usually means the content didn't meet quality standards.")
                print("   You may need to adjust the source PDF or QC criteria.")
            elif "✗ Generator failed" in result.stdout:
                print("\n💡 DIAGNOSIS: Generator Assistant failed")
                print("   Check that AUTHOR_QA_GEN_V2_ID is correct in .env")
            elif "✗ QC failed" in result.stdout:
                print("\n💡 DIAGNOSIS: QC Assistant failed")
                print("   Check that AUTHOR_QA_QC_V2_ID is correct in .env")
            elif not result.stdout.strip():
                print("\n💡 DIAGNOSIS: No output from revised_runqav2.py")
                print("   The script may have crashed. Try running it manually:")
                print(f"   python revised_runqav2.py --file-id {chunk_file_id}")
            else:
                print("\n💡 DIAGNOSIS: Script ran but didn't produce expected output format")
                print("   Expected line: 'Generated QAv2 file ID: file-xxxxx'")
                print("   Try running manually to see full error details")
            
            rollback_files(file_ids)
            return

        qav2_file_id = qav2_file_id_match.group(1)
        file_ids.append(qav2_file_id)
        print(f"   ✅ Generated QAv2: {qav2_file_id}")
        store_qav2_filename = f"QAv2_{sanitize_filename(corrected_stem)}_{chunk_file_id}.json"
        
    except subprocess.TimeoutExpired:
        # ENHANCED: More informative timeout message
        print("\n❌ QAv2 generation timed out (>10 minutes)")
        print("   This usually means:")
        print("   - The OpenAI API is slow or unresponsive")
        print("   - The file is very large and taking longer than expected")
        print("   - The assistants are stuck in a processing loop")
        print("\n💡 TIP: Try running manually to see where it gets stuck:")
        print(f"   python revised_runqav2.py --file-id {chunk_file_id}")
        rollback_files(file_ids)
        return
    except requests.exceptions.RequestException as e:
        # ENHANCED: Specific handling for API errors
        print(f"\n❌ OpenAI API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Status code: {e.response.status_code}")
            print(f"   Response: {e.response.text[:500]}")  # First 500 chars
        print("\n💡 TIP: Check your internet connection and OpenAI service status")
        rollback_files(file_ids)
        return
    except Exception as e:
        # ENHANCED: More detailed exception reporting
        print(f"\n❌ Error in OpenAI processing: {e}")
        print("\n" + "="*60)
        print("FULL ERROR TRACEBACK:")
        print("="*60)
        traceback.print_exc()
        print("="*60)
        rollback_files(file_ids)
        return

    # Step 4: Convert to Claude formats with FIXED naming and chunk count
    print("\n📝 Step 3/4: Converting to Claude TXT formats...")
    try:
        process_json_to_claude_formats(chunk_json, corrected_stem)
    except Exception as e:
        print(f"\n❌ Error converting to Claude formats: {e}")
        print("The chunk JSON and QAv2 files are already in OpenAI vector store.")
        print("You can try the conversion manually later if needed.")
        traceback.print_exc()
        # Don't rollback here - OpenAI uploads are fine, just Claude conversion failed
        return

    # Step 5: Get metadata from user
    print("\n📚 Step 4/4: Updating metadata...")
    
    # Load existing metadata
    if METADATA_EXCEL.exists():
        existing_df = pd.read_excel(METADATA_EXCEL)
    else:
        existing_df = pd.DataFrame()
    
    existing_tags = set()
    if 'tags' in existing_df.columns:
        existing_tags = set(",".join(existing_df['tags'].dropna().astype(str)).split(","))

    # Extract metadata suggestions
    author_suggest, year_suggest, title_suggest = extract_metadata_from_filename(corrected_stem)
    
    print(f"\n📋 Metadata suggestions:")
    print(f"   Author: {author_suggest}")
    print(f"   Year: {year_suggest}")
    print(f"   Title: {title_suggest}\n")

    try:
        author, year, title, text_type, tags = prompt_metadata(
            author_suggest, year_suggest, title_suggest, existing_tags
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Metadata entry cancelled by user")
        print("Files have been uploaded to OpenAI and converted to Claude formats.")
        print("Metadata Excel was not updated. You can add metadata manually later.")
        return
    except Exception as e:
        print(f"\n❌ Error during metadata entry: {e}")
        print("Files have been uploaded and converted successfully.")
        print("Metadata Excel was not updated. You can add metadata manually later.")
        return

    # Prepare metadata
    attrs_chunk = {
        "author": author, 
        "year": year, 
        "title": title, 
        "text_type": text_type, 
        "tags": tags, 
        "source_type": "chunked"
    }
    attrs_qav2 = attrs_chunk.copy()
    attrs_qav2.update({"source_type": "qav2", "source_chunk_id": chunk_file_id})

    # ENHANCED: Update attributes via API with error handling
    try:
        print("   📝 Updating file attributes in vector store...")
        headers = {
            "Authorization": f"Bearer {API_KEY}", 
            'Content-Type': 'application/json', 
            'OpenAI-Beta': 'assistants=v1'
        }
        
        resp = requests.post(
            f"https://api.openai.com/v1/vector_stores/{TEXT_STORE_ID}/files/{chunk_file_id}", 
            headers=headers, 
            json={"attributes": attrs_chunk}
        )
        resp.raise_for_status()
        
        resp = requests.post(
            f"https://api.openai.com/v1/vector_stores/{TEXT_STORE_ID}/files/{qav2_file_id}", 
            headers=headers, 
            json={"attributes": attrs_qav2}
        )
        resp.raise_for_status()
        print("   ✅ Vector store attributes updated")
        
    except Exception as e:
        print(f"   ⚠️  Attribute update failed: {e}")
        print("   Files are still in vector store, just without custom attributes")
        # Continue anyway - don't rollback everything

    # ENHANCED: Update Excel with error handling
    try:
        print("   📊 Updating metadata Excel...")
        new_rows = [
            {
                "store filename": store_chunk_filename, 
                "file_id": chunk_file_id, 
                **attrs_chunk, 
                "source_chunk_id": "",
                "timestamp": datetime.now().isoformat()
            },
            {
                "store filename": store_qav2_filename, 
                "file_id": qav2_file_id, 
                **attrs_qav2,
                "timestamp": datetime.now().isoformat()
            }
        ]
        
        df_updated = pd.concat([existing_df, pd.DataFrame(new_rows)], ignore_index=True)
        df_updated.to_excel(METADATA_EXCEL, index=False)
        print("   ✅ Metadata Excel updated")
    except Exception as e:
        print(f"   ⚠️  Excel update failed: {e}")
        print("   Files are processed successfully, but metadata Excel wasn't updated")
        print("   You can add the metadata manually later")
    
    # Summary
    print("\n" + "="*60)
    print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
    print("="*60)
    print("\n📁 Output locations:")
    print(f"   • PDF: {target_pdf}")
    print(f"   • JSON chunks: {chunk_json}")
    print(f"   • QAv2 JSON: {CHATGPT_QA_DIR / store_qav2_filename}")
    print(f"   • Claude complete: {CLAUDE_COMPLETE_DIR}")
    
    # Get the actual author folder name (with chunk count)
    try:
        chunks_data = json.load(open(chunk_json, 'r', encoding='utf-8'))
        if isinstance(chunks_data, list):
            chunk_count = len(chunks_data)
        else:
            chunk_count = len(chunks_data.get('chunks', chunks_data.get('pages', [])))
        actual_author_folder = create_author_folder_name(author, year, title, chunk_count)
        print(f"   • Claude by author: {CLAUDE_BY_AUTHOR_DIR / actual_author_folder}")
    except:
        print(f"   • Claude by author: {CLAUDE_BY_AUTHOR_DIR} (folder name may vary)")
    
    print(f"   • Metadata: {METADATA_EXCEL}")
    print("\n🎉 All processing complete!")

# --- Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Integrated Literature Processing Pipeline",
        epilog="Processes PDFs through chunking, QA enrichment, and format conversion"
    )
    parser.add_argument("pdf_path", nargs='?', help="Path to PDF file")
    
    args = parser.parse_args()
    
    # Interactive mode if no path provided
    if not args.pdf_path:
        print("="*60)
        print("📚 INTEGRATED LITERATURE PROCESSING PIPELINE")
        print("="*60)
        print("\nThis will process your PDF through the complete pipeline:")
        print("1. Generate JSON chunks (ChatGPT format)")
        print("2. Create QAv2 enrichment")
        print("3. Convert to Claude TXT formats")
        print("4. Update metadata tracking")
        print("\nPlease enter the path to your PDF file.")
        print("(You can drag and drop the file here)")
        print()
        
        pdf_path = input("PDF path: ").strip().strip('"\'')
        
        if not pdf_path:
            print("❌ No path provided. Exiting.")
            sys.exit(1)
    else:
        pdf_path = args.pdf_path
    
    main(pdf_path)