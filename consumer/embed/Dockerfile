# Local bge-m3 embedding service. Built on the consumer's machine (native arch), so it
# runs on both x86_64 and arm64 (Apple Silicon) — unlike the amd64-only TEI images.
# The bge-m3 weights are downloaded from Hugging Face on first start (cached in a volume),
# so this image redistributes no model — same model-pull pattern as the engine images.
FROM python:3.12-slim

WORKDIR /app

# Install CPU-only torch first (the default wheel bundles the multi-GB CUDA stack, dead
# weight for a CPU embedder). sentence-transformers then reuses this already-satisfied
# torch instead of pulling the CUDA build. GPU users can swap to a CUDA wheel.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
