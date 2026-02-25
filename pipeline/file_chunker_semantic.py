"""
Enhanced semantic, overlap‑aware PDF‑to‑JSON chunker
(modified to remove chunking_summary.json generation)
-----------------------------------------------------------------
• Emits an **array** of chunk objects as <basename>.json
• Skips PDFs whose JSON is newer (idempotent)
• Supports either an entire folder (--source) **or** a single file (--file)

CLI examples
------------
Folder mode:
    python file_chunker_semantic.py -s "H:/PDFs" -o "JSONs" -m 600 -r 0.2 -n 80
Single‑file mode (used by integrated-pipeline.py):
    python file_chunker_semantic.py --file "H:/PDFs/Book.pdf"

Dependencies
------------
    pip install pymupdf spacy tiktoken tqdm unidecode rich python-dotenv
    python -m spacy download en_core_web_sm
"""

from __future__ import annotations
import os, re, json, hashlib, argparse, logging
from typing import List, Optional, Dict, Any

import fitz                      # PyMuPDF
import tiktoken
import spacy
from tqdm import tqdm
from unidecode import unidecode

# ───────────────────── CLI & CONFIG ────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Semantic PDF → JSON chunker")
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument("--source", "-s", default=os.getcwd(),
                     help="Folder containing PDFs to chunk (default: cwd)")
    src.add_argument("--file", help="Process only this PDF")
    parser.add_argument("--output", "-o", default=None,
                        help="Output folder for JSONs (default: <source>/JSONs)")
    parser.add_argument("--max_tokens", "-m", type=int, default=600)
    parser.add_argument("--overlap_ratio", "-r", type=float, default=0.2)
    parser.add_argument("--min_tokens", "-n", type=int, default=80)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()

args = parse_args()
SOURCE_FOLDER = os.path.abspath(os.path.dirname(args.file) if args.file else args.source)
OUT_FOLDER    = os.path.join(SOURCE_FOLDER, args.output or "JSONs")
MAX_TOKENS    = args.max_tokens
OVERLAP_RATIO = args.overlap_ratio
MIN_TOKENS    = args.min_tokens

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s",
                    level=logging.DEBUG if args.verbose else logging.INFO)
log = logging.getLogger("chunker")

# ───────────────────── Regex / Helpers ─────────────────────────

FILENAME_RE = re.compile(r"(.+?)\s+\((\d{4})\)\s*[\-–—]\s*(.+?)\.pdf$", re.I)
AUTHOR_MAP = {
    "Srnicek, N.": "Nick Srnicek",
    "Varoufakis, Y.": "Yanis Varoufakis",
    "van Dijck, J.": "José van Dijck",
    "Gillespie, T.": "Tarleton Gillespie",
    "Bucher, T.": "Taina Bucher",
}

ENC = tiktoken.get_encoding("cl100k_base")
NLP = spacy.load("en_core_web_sm", disable=["tagger", "ner", "lemmatizer"])

n_tokens = lambda txt: len(ENC.encode(txt))
sha1_8   = lambda txt: hashlib.sha1(txt.encode()).hexdigest()[:8]

def clean(txt: str) -> str:
    txt = unidecode(txt)
    txt = re.sub(r"-\n\s*", "", txt)
    txt = re.sub(r"\s*\n\s*", " ", txt)
    return re.sub(r"\s{2,}", " ", txt).strip()

def sent_split(paragraph: str) -> List[str]:
    return [s.text.strip() for s in NLP(paragraph).sents if len(s.text.split()) > 3]

HEAD_RE = re.compile(r"^(?:[A-Z][A-Z\s]{5,}|(?:Chapter|Section)\s+\d+)")
heading   = lambda s: s.title() if HEAD_RE.match(s) else None
sanitize   = lambda name: re.sub(r'[<>:"/\\|?*]', '_', name)

# ───────────────────── Metadata ────────────────────────────────

def extract_metadata(pdf_path: str):
    fn = os.path.basename(pdf_path)
    m = FILENAME_RE.match(fn)
    if m:
        auth_raw, yr, title = m.groups()
        auth = AUTHOR_MAP.get(auth_raw.strip(), auth_raw.strip())
        return auth, int(yr), title

    with fitz.open(pdf_path) as d:
        md = d.metadata
    title = md.get("title") or os.path.splitext(fn)[0]
    return md.get("author", "Unknown"), None, title

# ───────────────────── Core chunking ───────────────────────────

def chunk_pdf(path: str) -> List[Dict[str, Any]]:
    doc = fitz.open(path)
    chunks, bucket, tok_ct = [], [], 0
    section, page_start = "Introduction", 1

    for page in doc:
        for sent in sent_split(clean(page.get_text())):
            if h := heading(sent):
                section = h
            t = n_tokens(sent)
            if tok_ct + t <= MAX_TOKENS:
                bucket.append(sent); tok_ct += t
            else:
                if tok_ct >= MIN_TOKENS:
                    txt = " ".join(bucket)
                    chunks.append({
                        "chunk_id": f"{os.path.basename(path)}#p{page_start}_{sha1_8(txt)}",
                        "text": txt,
                        "section": section,
                        "page_start": page_start
                    })
                # overlap back‑shift
                need = int(MAX_TOKENS * OVERLAP_RATIO)
                back = []
                while bucket and n_tokens(" ".join(back)) < need:
                    back.insert(0, bucket.pop())
                bucket = back + [sent]
                tok_ct = n_tokens(" ".join(bucket))
                page_start = page.number + 1

    if tok_ct >= MIN_TOKENS:
        txt = " ".join(bucket)
        chunks.append({
            "chunk_id": f"{os.path.basename(path)}#p{page_start}_{sha1_8(txt)}",
            "text": txt,
            "section": section,
            "page_start": page_start
        })
    doc.close()
    return chunks

# ───────────────────── JSON writer ─────────────────────────────

def write_json(path: str, records: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

# ───────────────────── Main ────────────────────────────────────

def main():
    os.makedirs(OUT_FOLDER, exist_ok=True)
    pdfs = [os.path.basename(args.file)] if args.file else \
           sorted(f for f in os.listdir(SOURCE_FOLDER) if f.lower().endswith(".pdf"))

    for pdf in tqdm(pdfs, desc="Chunking PDFs"):
        src = os.path.join(SOURCE_FOLDER, pdf)
        auth, yr, title = extract_metadata(src)
        safe = sanitize(os.path.splitext(pdf)[0])
        out_f = os.path.join(OUT_FOLDER, f"{safe}.json")

        if os.path.exists(out_f) and os.path.getmtime(out_f) >= os.path.getmtime(src):
            log.info("Skipping up‑to‑date: %s", pdf)
            continue

        try:
            chunks = chunk_pdf(src)
            for c in chunks:
                c.update({"author": auth, "year": yr, "title": title, "source": pdf})
            write_json(out_f, chunks)
            log.info("Processed %s: %d chunks → %s", pdf, len(chunks), out_f)
        except Exception as e:
            log.error("Error chunking %s: %s", pdf, e)

    log.info("Chunking complete.")

if __name__ == "__main__":
    main()