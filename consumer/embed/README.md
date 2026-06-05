# consumer/embed — the local bge-m3 embedding service

> _"That rug really tied the room together."_ — The Dude

This little service is the rug. It's the beverage that ties the whole search together. Take it away and `vector_search` has nothing to search _with_. Even though the consumer adds no data of its own, search still has to turn the user's **query** into a vector at search time — and that's this service's whole job. The Dude abides; so does the embedder.

## What it does

It serves one endpoint, `/embed`:

```
POST /embed   {"texts": ["..."]}  ->  {"embeddings": [[...]]}
```

Each input text comes back as an **L2-normalized, 1024-dimensional bge-m3 vector** — the exact same space the shared `technology` collection was built in (validated: cosine 1.0 against the builder's own output). Same space in, same space out, so a query lands right next to the documents it should match. There's also a `/health` endpoint that reports readiness, the model name, and the dimension.

A note on being precise, as Maude would want: the request accepts an optional `index` field for API parity, but it's ignored — there's a single model here.

## How it's built

- **CPU-only torch.** The default torch wheel drags along the multi-GB CUDA stack — dead weight for a CPU embedder. We install the CPU build first, so the image lands around **~1.3 GB** instead of **~5.4 GB**. (GPU users can swap in a CUDA wheel. Careful, man — there's a beverage here.)
- **Multi-arch.** It builds natively on **x86_64 _and_ arm64 (Apple Silicon)**, since you build it on your own machine.
- **No bundled model.** The bge-m3 weights (~2 GB, MIT license) download from Hugging Face on first start and are cached in a volume. This repo redistributes no model.

## Files

| File               | Purpose                                                        |
| ------------------ | -------------------------------------------------------------- |
| `app.py`           | FastAPI app: loads bge-m3 once, serves `/embed` and `/health`. |
| `Dockerfile`       | Builds the image; installs CPU-only torch, then the rest.      |
| `requirements.txt` | fastapi, uvicorn, sentence-transformers.                       |

## How it's wired in

It comes up as the `embed` service in `consumer/docker-compose.yml` on **port 8100**, and the bundled MCP's `EMBED_URL` points straight at it. The MCP asks for a query embedding, this service hands one back, Qdrant does the rest. That's just, like, the way it works, man.
