from dotenv import load_dotenv
import os
import itertools
import requests
import openai
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# Load environment variables — thesis .env has TEXT_STORE_ID and INTERNAL_API_KEY,
# dev .env has OPENAI_API_KEY
load_dotenv(r"H:\My Drive\Thesis\Literature\.env")
load_dotenv(r"C:\Users\bhara\dev\.env", override=True)

# ---------- ENV ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VECTOR_STORE_ID = os.getenv("TEXT_STORE_ID")
INTERNAL_KEY = os.getenv("INTERNAL_API_KEY")
if not INTERNAL_KEY:
    raise RuntimeError("INTERNAL_API_KEY not set in .env")

if not VECTOR_STORE_ID:
    raise RuntimeError("TEXT_STORE_ID not set in .env")

openai.api_key = OPENAI_API_KEY
_debug = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
app = FastAPI(
    docs_url="/docs" if _debug else None,
    redoc_url="/redoc" if _debug else None,
    openapi_url="/openapi.json" if _debug else None,
)

# ---------- Pydantic models ----------
class TextRef(BaseModel):
    author: str | None = None
    year: str | None = None
    title: str | None = None

class ReferenceRequest(BaseModel):
    references: list[TextRef] = Field(..., description="List of reference filters")
    author_token: str | None = Field(None, description="Optional token to filter by author")
    tags: list[str] | None = Field(None, description="Optional list of tags to filter by")

    class Config:
        extra = "forbid"

class DocumentOut(BaseModel):
    doc_id: str
    source_type: str | None
    author: str | None
    year: str | None
    title: str | None
    text_type: str | None
    tags: list[str]
    source_chunk_id: str | None
    content: str

class FetchResponse(BaseModel):
    needs_disambiguation: bool
    candidates: list[dict] | None = None
    docs: list[DocumentOut]

# ---------- Helpers ----------
def matches(ref: TextRef, attr: dict) -> bool:
    # robust author match (singular/plural fallback)
    author_blob = (attr.get("authors") or attr.get("author") or "").lower()
    if ref.author and ref.author.lower() not in author_blob:
        return False
    # year filter
    if ref.year and str(ref.year) != str(attr.get("year") or ""):
        return False
    # title filter
    if ref.title and ref.title.lower() not in (attr.get("title") or "").lower():
        return False
    return True

# ---------- REST iterator ----------
def page_iter():
    after = None
    headers = {
        "Authorization": f"Bearer {openai.api_key}",
        "OpenAI-Beta": "assistants=v1",
    }
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        url = f"https://api.openai.com/v1/vector_stores/{VECTOR_STORE_ID}/files"
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
        except requests.HTTPError:
            err = response.json().get("error", {})
            msg = err.get("message", response.text)
            raise HTTPException(status_code=502, detail=f"Vector store fetch error: {msg}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Unexpected error fetching vector store: {e}")

        data = response.json().get("data", [])
        if not data:
            break
        for d in data:
            class Stub:
                def __init__(self, item):
                    self.id = item.get("id")
                    attrs = item.get("attributes") or item.get("metadata") or {}
                    self.attributes = attrs
            yield Stub(d)
        after = data[-1].get("id")

# ---------- Routes ----------
@app.get("/ping")
def ping() -> dict:
    return {"ping": "pong"}

@app.post("/fetch_texts", response_model=FetchResponse)
def fetch(req: ReferenceRequest, x_api_key: str = Header(..., alias="X-API-KEY")):
    if x_api_key != INTERNAL_KEY:
        raise HTTPException(status_code=401, detail="Bad key")

    chunks: list = []
    qas: list = []
    for stub in page_iter():
        attr = stub.attributes or {}
        # author_token filter (robust fallback)
        if req.author_token:
            author_blob = (attr.get("authors") or attr.get("author") or "").lower()
            if req.author_token.lower() not in author_blob:
                continue
        # tags filter, normalize single string to list
        raw_tags = attr.get("tags")
        tags_attr = [raw_tags] if isinstance(raw_tags, str) else (raw_tags or [])
        if req.tags and not any(t in tags_attr for t in req.tags):
            continue
        # reference filters
        if any(matches(r, attr) for r in req.references):
            stype = (attr.get("source_type") or "").lower()
            if stype.startswith("chunk"):
                chunks.append(stub)
            else:
                qas.append(stub)

    # build unique reference bases
    bases = {(c.attributes.get("author"), c.attributes.get("year"), c.attributes.get("title")) for c in chunks}
    # if user only provided an author and multiple matches found, trigger disambiguation
    only_author_search = (
        len(req.references) == 1
        and req.references[0].author
        and not req.references[0].year
        and not req.references[0].title
    )
    if only_author_search and len(bases) > 1:
        candidates = [{"author": a, "year": y, "title": t} for a, y, t in bases]
        return FetchResponse(needs_disambiguation=True, candidates=candidates, docs=[])

    # existing broad disambiguation threshold
    if len(bases) > 3:
        candidates = [{"author": a, "year": y, "title": t} for a, y, t in itertools.islice(bases, 10)]
        return FetchResponse(needs_disambiguation=True, candidates=candidates, docs=[])

    # map QA back to chunks and assemble docs
    chunk_map = {c.id: c for c in chunks}
    docs: list = []
    for qa in qas:
        cid = qa.attributes.get("source_chunk_id")
        if cid in chunk_map:
            docs.append(chunk_map[cid])
            docs.append(qa)
    for c in chunks:
        if c not in docs and len(docs) < 6:
            docs.append(c)

    # prepare output
    out: list[DocumentOut] = []
    for stub in docs[:6]:
        # HTTP-based content fetch from vector store
        try:
            content_url = (
                f"https://api.openai.com/v1/vector_stores/"
                f"{VECTOR_STORE_ID}/files/{stub.id}/content"
            )
            resp = requests.get(
                content_url,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v1",
                },
            )
            resp.raise_for_status()
            text_blob = resp.text
        except Exception:
            text_blob = ""

        raw_tags = stub.attributes.get("tags")
        tags_list = [raw_tags] if isinstance(raw_tags, str) else (raw_tags or [])
        doc = DocumentOut(
            doc_id=stub.id,
            source_type=stub.attributes.get("source_type"),
            author=stub.attributes.get("author"),
            year=stub.attributes.get("year"),
            title=stub.attributes.get("title"),
            text_type=stub.attributes.get("text_type"),
            tags=tags_list,
            source_chunk_id=stub.attributes.get("source_chunk_id"),
            content=text_blob[:8000],
        )
        out.append(doc)

    return FetchResponse(needs_disambiguation=False, candidates=None, docs=out)
