---
name: book-qa
description: |
  Use when the user asks about books, reading, book Q&A, or wants to import/query/analyze ebooks.
  This skill manages a local RAG-based book Q&A system at ~/book-qa/ that ingests PDF/epub/txt,
  chunks and embeds them via Bailian API, stores in ChromaDB, and answers questions via LLM.
  Triggers: 书, 读书, 书籍, PDF, epub, 识别书, 总结书, 问书
---

# Book QA — Local RAG Book Q&A System

Project at `~/book-qa/`:

1. **Ingest**: `python3 ingest.py book.pdf` → extract text → chunk → embed → store in ChromaDB
2. **Query**: `python3 query.py "问题"` → embed question → search similar chunks → LLM answers with context
3. **List**: `python3 list.py` → list books / `--summarize` for AI summary

## Architecture

```
config.json  ← providers / model_defs / chunking / QA prompts
config.py    ← shared: load_config, resolve_step, BailianEmbedding, get_chroma_client
ingest.py    ← import PDF/epub/txt → chunk → embed → ChromaDB
query.py     ← semantic search + LLM Q&A (single question or --chat interactive)
list.py      ← list books / --summarize AI summary per book
books/       ← raw book files (auto-copied on import)
db/          ← ChromaDB persistent storage
.env → ~/bilibili-pipeline/.env
```

## Dependencies

```bash
pip install pymupdf ebooklib chromadb openai beautifulsoup4 lxml
```

## Usage

```bash
cd ~/book-qa

# Import books
python3 ingest.py book.pdf
python3 ingest.py book.epub
python3 ingest.py book.txt
python3 ingest.py books/       # batch import directory

# Ask questions
python3 query.py "这本书主要讲了什么？"
python3 query.py --book "Python" "函数怎么定义"   # filter by book title
python3 query.py --chat                            # interactive mode

# In chat mode:
#   >>> 正常提问
#   >>> /book Python     ← 过滤到包含「Python」的书
#   >>> /all             ← 清除过滤
#   >>> quit

# List and summarize
python3 list.py                 # list all books
python3 list.py --summarize     # AI summary for each book
python3 list.py --book "书名"   # show chunks for a specific book
```

## Configuration (config.json)

Same pattern as bilibili-pipeline:

| Key | Description |
|-----|-------------|
| `providers` | API providers (deepseek, bailian) |
| `model_defs` | Model definitions (provider + pricing) |
| `qa` | QA LLM config (model, temperature, max_tokens) |
| `embed` | Embedding model config (model, dimension) |
| `chunking` | Chunk size (800 chars) and overlap (160) |
| `search` | n_results (5) and min_score (0.3) |
| `paths` | books dir and db dir |
| `prompts` | System prompts for QA and summarize |

## Supported Formats

- **PDF**: Full text extraction via PyMuPDF, metadata (title/author)
- **epub**: HTML parsing via ebooklib + BeautifulSoup
- **TXT**: Plain text, auto-detects title from `# title` or `《title》`

## Cost

| Step | Model | Price |
|------|-------|-------|
| Embedding | Bailian text-embedding-v3 | free tier / ¥0 |
| QA | Agnes-2.0-Flash | **FREE** |

## File Locations

- Scripts: `~/book-qa/*.py`
- Config: `~/book-qa/config.json`
- Books: `~/book-qa/books/`
- DB: `~/book-qa/db/`
- Env: `~/book-qa/.env` (symlinked to `~/bilibili-pipeline/.env`)
