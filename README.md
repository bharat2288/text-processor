# Text Processor

A deterministic literature retrieval system built for academic research. Converts PDFs into semantically chunked text, enriches them with structured Q&A pairs via OpenAI Assistants, stores everything in vector stores, and serves it through a FastAPI endpoint to a CustomGPT — ensuring every answer is traceable to a specific passage in a specific source text.

Built during my dissertation (2024-2026) to solve a concrete problem: querying 315 academic PDFs and getting back faithful, citation-grounded responses instead of LLM hallucinations. This was early "context engineering" — building retrieval infrastructure before RAG was a product category.

## What it does

- **Semantic chunking** of academic PDFs using spaCy sentence segmentation + tiktoken token counting (~600 tokens per chunk, 20% overlap)
- **Q&A enrichment** via two-stage OpenAI Assistants pipeline: a Generator creates structured question-answer pairs from each chunk, a QC Assistant validates them against the source text
- **Vector store integration** uploading chunks + QA pairs to OpenAI's vector store with structured metadata (author, year, title, tags)
- **FastAPI retrieval server** that queries the vector store by author/year/title, handles disambiguation, and returns grounded document content
- **CustomGPT interface** constrained to only synthesize from retrieved documents — no generative hallucination

## Architecture

```
PDF corpus (315 files)
    │
    ▼
[file_chunker_semantic.py]     PyMuPDF + spaCy + tiktoken
    │                          ~600 token chunks, 20% overlap
    ▼
[integrated_pipeline.py]       OpenAI file upload + vector store
    │                          Metadata: author, year, title, tags
    ├──► QA Generator Assistant
    │        │
    │        ▼
    │    QC Validator Assistant
    │        │
    │        ▼
    │    QA pairs → vector store
    │
    ▼
[server.py]                    FastAPI on localhost
    │                          Queries vector store via OpenAI REST API
    │                          Author/year/title filtering + disambiguation
    ▼
CustomGPT                      Retrieval-grounded synthesis
                               Every claim traced to source chunk
```

## Two pipeline versions

### v1.0: Full Pipeline (`integrated_pipeline.py`)

Cloud-integrated processing with OpenAI. For each PDF:
1. Extract text and chunk semantically
2. Upload chunks to OpenAI file storage + vector store
3. Generate Q&A pairs via custom Assistant
4. QC-validate Q&A against source text
5. Upload validated Q&A to vector store
6. Track metadata in Excel

**Trade-off:** High fidelity enrichment, but slow (~3 min per PDF for QA generation), expensive (API costs), and fragile (rate limits, encoding issues).

### v2.0: Simple Pipeline (`simple_pipeline.py`)

Local-only processing. PDF in, semantic text chunks out. No API calls, no uploads. 6x faster, zero cost, no encoding issues. Used for the final batch of PDFs after the enrichment approach was validated.

Both versions write to the same output directory structure and can coexist.

## Context engineering before RAG

This system was built in early 2024, before "RAG" was a standard product category. The design decisions reflect the constraints of that moment:

- **LLM hallucination was worse.** In 2024, asking ChatGPT to cite an academic text would frequently produce fabricated quotes and invented page numbers. The entire system exists because you couldn't trust generative responses about source material.
- **No off-the-shelf RAG tools.** There was no LangChain RAG template, no Pinecone starter kit, no "upload your docs" product. Building retrieval meant writing the chunking, the metadata schema, the API layer, and the prompt constraints yourself.
- **Two-stage QA validation was novel.** Using one Assistant to generate structured Q&A and another to validate it against the source text was an early form of what's now called "self-consistency checking" or "LLM-as-judge."
- **The CustomGPT as constrained interface.** Rather than building a chat UI, the system used OpenAI's CustomGPT as a retrieval-grounded front-end — the instructions explicitly forbid the model from generating content not traceable to the vector store.

The result: a system that processed 315 academic PDFs into 227 enriched QA files, stored in a vector store with structured metadata, queryable through natural language with deterministic grounding. Every answer pointed back to a specific chunk from a specific text.

## Screenshots

### CustomGPT retrieval with grounded citations
![CustomGPT retrieval](screenshots/Screenshot%202026-02-25%20133524.png)
*Querying "Latour, 2005" — the CustomGPT calls the FastAPI server, retrieves chunks from the vector store, and synthesizes a response with QA citations ([QA-Latour-1], [QA-Latour-3]) traceable to specific source passages.*

### Full pipeline run: chunking, upload, QA generation, metadata
![Pipeline run](screenshots/Screenshot%202026-02-25%20134705.png)
*The integrated pipeline processing a PDF through all 4 steps: semantic chunking (134 chunks), OpenAI upload + QA generation, TXT conversion, and interactive metadata entry with tag selection. Shows "PIPELINE COMPLETED SUCCESSFULLY" with all output locations.*

### QA enrichment output: structured question-answer pairs
![QA JSON output](screenshots/Screenshot%202026-02-25%20134930.png)
*A generated QAv2 JSON file showing structured enrichment: major claims, salient aspects, theoretical concepts, and question-answer pairs — all grounded in the source text and validated by the QC Assistant.*

## Scale

| Metric | Count |
|--------|-------|
| PDFs processed | 315 |
| QA files generated (v2) | 227 |
| Vector store files | ~1,100 |
| Chunking parameters | 600 tokens max, 20% overlap, 80 token minimum |
| Processing time (v1.0) | ~3 min per PDF |
| Processing time (v2.0) | ~30 sec per PDF |

## Setup

### Prerequisites

- Python 3.10+
- OpenAI API key (for v1.0 pipeline and server)
- spaCy English model: `python -m spacy download en_core_web_sm`

### Install

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### Environment variables

Copy `.env.example` to `.env` and fill in your keys. The simple pipeline (v2.0) requires no API keys.

| Variable | Required for | Description |
|----------|-------------|-------------|
| `OPENAI_API_KEY` | v1.0 pipeline, server | OpenAI API key |
| `TEXT_STORE_ID` | v1.0 pipeline, server | OpenAI vector store ID |
| `AUTHOR_QA_GEN_V2_ID` | v1.0 pipeline | QA Generator Assistant ID |
| `AUTHOR_QA_QC_V2_ID` | v1.0 pipeline | QA Validator Assistant ID |
| `INTERNAL_API_KEY` | server | API key for server authentication |

### Running the server

```bash
cd server
uvicorn server:app --reload --port 8200
```

### Running the pipelines

```bash
# Simple pipeline (local-only, no API keys needed)
cd pipeline
python simple_pipeline.py "path/to/Author (Year) - Title.pdf"

# Full pipeline (requires OpenAI API keys)
python integrated_pipeline.py "path/to/Author (Year) - Title.pdf"
```

## Project structure

```
text-processor/
├── pipeline/
│   ├── integrated_pipeline.py     # v1.0: full cloud pipeline
│   ├── simple_pipeline.py         # v2.0: local-only chunking
│   ├── file_chunker_semantic.py   # Core semantic chunking engine
│   └── config.py                  # Central path + API configuration
├── server/
│   ├── server.py                  # FastAPI retrieval endpoint
│   └── customgpt_instructions.md  # CustomGPT system prompt
├── docs/
│   └── PROJECT_DOCUMENTATION.md   # Comprehensive development history
├── specs/                         # Design docs and session notes
├── screenshots/                   # System screenshots
├── .env.example
├── requirements.txt
└── .gitignore
```

## API endpoint

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ping` | Health check |
| `POST` | `/fetch_texts` | Query vector store by author/year/title, returns grounded documents |

## Research context

This tool was built for my dissertation at UC Berkeley on how platform discourse communities teach participation without formal instruction. The literature corpus spans theory (Deleuze, Bourdieu, Foucault), ethnography (Zaloom, Ho, Miyazaki), platform studies, STS, economic anthropology, and learning sciences. The system enabled cross-cutting thematic queries across 315 sources — the kind of retrieval that keyword search can't do and that 2024-era LLMs couldn't do faithfully.
