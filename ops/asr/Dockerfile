# NVIDIA speech stack for Cadence: Parakeet ASR + Sortformer diarization (NeMo) on the GB10.
# Built on the vLLM NGC image so torch/CUDA already match the GB10 (its pip constraints keep torch pinned).
FROM cadence/vllm:ngc
ENV PIP_NO_BUILD_ISOLATION=0 HF_HOME=/models/hf PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "nemo_toolkit[asr]" python-multipart nltk
COPY server.py /app/server.py
WORKDIR /app
ENTRYPOINT ["python3", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001"]
