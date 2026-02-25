#!/usr/bin/env python3
"""
Simple Literature Processing Pipeline
-------------------------------------
Local-only processing: PDF → TXT chunks

NO external APIs, NO uploads, NO complex workflows
Just: PDF in → Semantic TXT chunks out → Metadata tracked

Usage:
    python simple_pipeline.py "path/to/Author (Year) - Title.pdf"
    python simple_pipeline.py  # Interactive mode
"""

import os
import sys
import re
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd

# PDF processing
import fitz  # PyMuPDF
import tiktoken
import spacy
from tqdm import tqdm
from unidecode import unidecode

# Import configuration (rename config_simple.py to config.py when using)
try:
    from config import (
        PDF_DIR, COMPLETE_DIR, BY_AUTHOR_DIR, METADATA_EXCEL,
        MAX_TOKENS, OVERLAP_RATIO, MIN_TOKENS
    )
except ImportError:
    from config_simple import (
        PDF_DIR, COMPLETE_DIR, BY_AUTHOR_DIR, METADATA_EXCEL,
        MAX_TOKENS, OVERLAP_RATIO, MIN_TOKENS
    )

# === INITIALIZATION ===
ENC = tiktoken.get_encoding("cl100k_base")
NLP = spacy.load("en_core_web_sm", disable=["tagger", "ner", "lemmatizer"])

# === HELPER FUNCTIONS ===
def n_tokens(text):
    """Count tokens in text"""
    return len(ENC.encode(text))

def clean_text(text):
    """Clean and normalize text"""
    text = unidecode(text)
    text = re.sub(r"-\n\s*", "", text)  # Remove hyphenation
    text = re.sub(r"\s*\n\s*", " ", text)  # Normalize newlines
    return re.sub(r"\s{2,}", " ", text).strip()

def sent_split(paragraph):
    """Split paragraph into sentences"""
    return [s.text.strip() for s in NLP(paragraph).sents if len(s.text.split()) > 3]

def is_heading(sentence):
    """Check if sentence looks like a heading"""
    HEAD_RE = re.compile(r"^(?:[A-Z][A-Z\s]{5,}|(?:Chapter|Section)\s+\d+)")
    if HEAD_RE.match(sentence):
        return sentence.title()
    return None

def sanitize_filename(name):
    """Clean filename for filesystem compatibility"""
    cleaned = re.sub(r'[^\w\-_. ]', '_', name)
    cleaned = re.sub(r'_{2,}', '_', cleaned)
    return cleaned.strip('_')

def correct_filename_format(filename):
    """Correct PDF filename to standard format: Author (Year) - Title"""
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
                return format_str.format(author, year, title if title else "Untitled")
    
    return filename

def extract_metadata_from_filename(filename):
    """Extract author, year, and title from filename"""
    patterns = [
        r'^(.*?)\s*\((\d{4})\)\s*[-–—]\s*(.+)$',
        r'^([^_]+)_(\d{4})_(.+)$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, filename)
        if match:
            author = match.group(1).strip()
            year = match.group(2)
            title = match.group(3).strip()
            
            author = sanitize_filename(author)
            return author, year, title
    
    # Fallback
    year_match = re.search(r'\b(\d{4})\b', filename)
    year = year_match.group(1) if year_match else "0000"
    parts = re.split(r'[_(),]', filename)
    author = sanitize_filename(parts[0]) if parts else "Unknown_Author"
    
    return author[:50], year, filename

def create_author_folder_name(author, year, title, chunk_count):
    """Create folder name: Author_Year_TitleWords_###chunks"""
    # Extract significant words from title
    main_title = re.split(r'[:;]', title)[0]
    words = re.split(r'[\s\-_]+', main_title)
    significant_words = []
    
    stopwords = {'the', 'and', 'for', 'with', 'from', 'in', 'on', 'at', 'to', 'of'}
    for word in words:
        if len(word) > 2 and word.lower() not in stopwords:
            significant_words.append(sanitize_filename(word))
        if len(significant_words) >= 3:
            break
    
    # Build folder name
    folder_parts = [author, year] + significant_words[:3]
    base_name = "_".join(folder_parts)
    folder_name = f"{base_name}_{chunk_count:03d}chunks"
    
    # Ensure reasonable length
    if len(folder_name) > 80:
        folder_parts = [author, year] + significant_words[:2]
        base_name = "_".join(folder_parts)
        folder_name = f"{base_name}_{chunk_count:03d}chunks"
    
    return folder_name

# === PDF CHUNKING ===
def chunk_pdf(pdf_path):
    """
    Chunk PDF into semantic segments with overlap
    
    Returns: List of chunk dictionaries with text, section, page info
    """
    doc = fitz.open(pdf_path)
    chunks = []
    bucket = []
    token_count = 0
    section = "Introduction"
    page_start = 1
    
    print(f"   Chunking {doc.page_count} pages...")
    
    for page in tqdm(doc, desc="   Processing pages", leave=False):
        page_text = clean_text(page.get_text())
        
        for sentence in sent_split(page_text):
            # Check for heading
            heading = is_heading(sentence)
            if heading:
                section = heading
            
            # Calculate tokens
            sent_tokens = n_tokens(sentence)
            
            # Add to bucket or create new chunk
            if token_count + sent_tokens <= MAX_TOKENS:
                bucket.append(sentence)
                token_count += sent_tokens
            else:
                # Save current chunk if it meets minimum
                if token_count >= MIN_TOKENS:
                    text = " ".join(bucket)
                    chunks.append({
                        "text": text,
                        "section": section,
                        "page_start": page_start,
                        "tokens": token_count
                    })
                
                # Create overlap
                overlap_target = int(MAX_TOKENS * OVERLAP_RATIO)
                overlap_bucket = []
                while bucket and n_tokens(" ".join(overlap_bucket)) < overlap_target:
                    overlap_bucket.insert(0, bucket.pop())
                
                # Start new chunk with overlap + current sentence
                bucket = overlap_bucket + [sentence]
                token_count = n_tokens(" ".join(bucket))
                page_start = page.number + 1
    
    # Save final chunk
    if token_count >= MIN_TOKENS:
        text = " ".join(bucket)
        chunks.append({
            "text": text,
            "section": section,
            "page_start": page_start,
            "tokens": token_count
        })
    
    doc.close()
    print(f"   [OK] Created {len(chunks)} chunks")
    return chunks

# === OUTPUT FUNCTIONS ===
def save_complete_text(chunks, author, year, title):
    """Save complete concatenated text file"""
    title_clean = sanitize_filename(title)
    if len(title_clean) > 50:
        title_clean = title_clean[:50]
    
    filename = f"LIT-{author}_{year}_{title_clean}_COMPLETE.txt"
    filepath = COMPLETE_DIR / filename
    
    # Ensure path isn't too long
    if len(str(filepath)) > 240:
        title_clean = title_clean[:30]
        filename = f"LIT-{author}_{year}_{title_clean}_COMPLETE.txt"
        filepath = COMPLETE_DIR / filename
    
    with open(filepath, 'w', encoding='utf-8') as f:
        # Header
        f.write("="*80 + "\n")
        f.write(f"COMPLETE TEXT\n")
        f.write(f"Author: {author}\n")
        f.write(f"Year: {year}\n")
        f.write(f"Title: {title}\n")
        f.write(f"Total Chunks: {len(chunks)}\n")
        f.write(f"Chunk Range: 001 - {len(chunks):03d}\n")
        f.write("="*80 + "\n\n")
        
        # Content
        for i, chunk in enumerate(chunks, 1):
            f.write(f"\n{'='*15} CHUNK {i:03d} {'='*15}\n")
            f.write(f"Section: {chunk['section']}\n")
            f.write(f"Page: {chunk['page_start']}\n")
            f.write(f"Tokens: {chunk['tokens']}\n")
            f.write(f"{'='*50}\n\n")
            f.write(chunk['text'].strip())
            f.write("\n\n")
    
    print(f"   [OK] Complete text: {filename}")
    return filepath

def save_author_chunks(chunks, author, year, title):
    """Save individual chunk files in author folder"""
    folder_name = create_author_folder_name(author, year, title, len(chunks))
    folder_path = BY_AUTHOR_DIR / folder_name
    folder_path.mkdir(exist_ok=True)
    
    # Prepare base name for chunks
    title_short = sanitize_filename(title)
    if len(title_short) > 40:
        title_short = title_short[:40]
    
    for i, chunk in enumerate(chunks, 1):
        chunk_filename = f"LIT-{author}-{title_short}-chunk_{i:03d}.txt"
        
        # Check path length
        full_path = folder_path / chunk_filename
        if len(str(full_path)) > 240:
            title_short = title_short[:20]
            chunk_filename = f"LIT-{author}-{title_short}-chunk_{i:03d}.txt"
        
        with open(folder_path / chunk_filename, 'w', encoding='utf-8') as f:
            f.write(f"Chunk {i:03d} of {len(chunks):03d}\n")
            f.write(f"Section: {chunk['section']}\n")
            f.write(f"Page: {chunk['page_start']}\n")
            f.write(f"Tokens: {chunk['tokens']}\n")
            f.write(f"{'='*50}\n\n")
            f.write(chunk['text'].strip())
    
    print(f"   [OK] Author chunks: {folder_name}/ ({len(chunks)} files)")
    return folder_path

# === METADATA MANAGEMENT ===
def update_metadata(pdf_path, author, year, title, chunk_count):
    """Update metadata Excel with processing info"""
    # Prepare new row
    new_row = {
        'pdf_filename': pdf_path.name,
        'author': author,
        'year': year,
        'title': title,
        'chunk_count': chunk_count,
        'processed_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'complete_file': f"LIT-{author}_{year}_*.txt",
        'author_folder': create_author_folder_name(author, year, title, chunk_count)
    }
    
    # Load or create Excel
    if METADATA_EXCEL.exists():
        try:
            df = pd.read_excel(METADATA_EXCEL)
        except:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()
    
    # Append new row
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    
    # Save
    df.to_excel(METADATA_EXCEL, index=False)
    print(f"   [OK] Metadata updated")

# === MAIN PIPELINE ===
def process_pdf(pdf_path):
    """Main pipeline: PDF → TXT chunks"""
    pdf_path = Path(pdf_path).resolve()
    
    if not pdf_path.exists():
        print(f"[ERROR] PDF not found: {pdf_path}")
        return False
    
    print("\n" + "="*60)
    print("SIMPLE LITERATURE PROCESSING PIPELINE")
    print("="*60)
    print(f"Processing: {pdf_path.name}")
    print(f"\nOutput directories:")
    print(f"  Complete: {COMPLETE_DIR}")
    print(f"  By Author: {BY_AUTHOR_DIR}")
    print()
    
    # Step 1: Copy to PDFs directory with corrected name
    print("[1/4] Preparing PDF...")
    corrected_stem = correct_filename_format(pdf_path.stem)
    target_pdf = PDF_DIR / f"{corrected_stem}.pdf"
    
    if pdf_path != target_pdf:
        shutil.copy(pdf_path, target_pdf)
        print(f"   [OK] Copied to: {target_pdf.name}")
    else:
        print(f"   [OK] Using: {target_pdf.name}")
    
    # Step 2: Extract metadata
    print("\n[2/4] Extracting metadata...")
    author, year, title = extract_metadata_from_filename(corrected_stem)
    print(f"   Author: {author}")
    print(f"   Year: {year}")
    print(f"   Title: {title[:60]}...")
    
    # Step 3: Chunk PDF
    print("\n[3/4] Chunking PDF...")
    chunks = chunk_pdf(target_pdf)
    
    # Step 4: Save outputs
    print("\n[4/4] Saving outputs...")
    save_complete_text(chunks, author, year, title)
    save_author_chunks(chunks, author, year, title)
    update_metadata(target_pdf, author, year, title, len(chunks))
    
    # Summary
    print("\n" + "="*60)
    print("PROCESSING COMPLETE!")
    print("="*60)
    print(f"Chunks created: {len(chunks)}")
    print(f"Complete text: {COMPLETE_DIR}")
    print(f"Author chunks: {BY_AUTHOR_DIR}")
    print(f"Metadata: {METADATA_EXCEL}")
    print("="*60 + "\n")
    
    return True

# === ENTRY POINT ===
def main():
    parser = argparse.ArgumentParser(
        description="Simple Literature Processing Pipeline - Local TXT chunking only",
        epilog="Example: python simple_pipeline.py 'Author (2024) - Title.pdf'"
    )
    parser.add_argument("pdf_path", nargs='?', help="Path to PDF file")
    
    args = parser.parse_args()
    
    # Interactive mode if no path provided
    if not args.pdf_path:
        print("="*60)
        print("SIMPLE LITERATURE PROCESSING PIPELINE")
        print("="*60)
        print("\nThis pipeline:")
        print("  1. Chunks your PDF semantically")
        print("  2. Saves as TXT files (no external APIs)")
        print("  3. Creates both complete and chunked versions")
        print("  4. Tracks metadata in Excel")
        print("\nDrag and drop your PDF file here:")
        print()
        
        pdf_path = input("PDF path: ").strip().strip('"\'')
        
        if not pdf_path:
            print("[ERROR] No path provided. Exiting.")
            sys.exit(1)
    else:
        pdf_path = args.pdf_path
    
    # Process the PDF
    success = process_pdf(pdf_path)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()