"""Cadence speech service: NVIDIA Parakeet (ASR, word timestamps) + Sortformer (speaker diarization).

POST /transcribe  (multipart file) -> {"segments": [{"start","end","speaker","text"}], "speakers": n, "timings": {...}}
Audio is converted to 16 kHz mono WAV with ffmpeg. Nothing is stored after the request.
"""
import os
import subprocess
import tempfile
import time

from fastapi import FastAPI, File, HTTPException, UploadFile


def _cpu_stft_patch():
    """cuFFT in this torch build fails on the GB10 (error 50) for torch.stft. Spectrograms are cheap, so compute
    the STFT on the Grace CPU and move the result back; the Parakeet/Sortformer networks still run on the GPU."""
    import torch
    if getattr(torch.stft, "_cadence_cpu", False):
        return
    orig = torch.stft

    def stft(input, *args, **kwargs):
        if input.is_cuda:
            dev = input.device
            args = tuple(a.cpu() if torch.is_tensor(a) else a for a in args)
            kwargs = {k: (v.cpu() if torch.is_tensor(v) else v) for k, v in kwargs.items()}
            return orig(input.cpu(), *args, **kwargs).to(dev)
        return orig(input, *args, **kwargs)
    stft._cadence_cpu = True
    torch.stft = stft

ASR_MODEL = os.environ.get("ASR_MODEL", "nvidia/parakeet-tdt-0.6b-v2")
DIAR_MODEL = os.environ.get("DIAR_MODEL", "nvidia/diar_streaming_sortformer_4spk-v2")
MAX_BYTES = 50 * 1024 * 1024
LOCAL_DIR = os.environ.get("NEMO_DIR", "/models/nemo")
app = FastAPI(title="cadence-asr")
_models: dict = {}


def models():
    if not _models:
        _cpu_stft_patch()
        import nemo.collections.asr as nemo_asr
        from nemo.collections.asr.models import SortformerEncLabelModel
        t0 = time.time()
        # Prefer local .nemo files (downloaded with curl; the HF client stalls on some networks).
        local = lambda name: os.path.join(LOCAL_DIR, name.split("/")[-1] + ".nemo")
        asr_path, diar_path = local(ASR_MODEL), local(DIAR_MODEL)
        _models["asr"] = (nemo_asr.models.ASRModel.restore_from(asr_path) if os.path.exists(asr_path)
                          else nemo_asr.models.ASRModel.from_pretrained(ASR_MODEL)).eval()
        _models["diar"] = (SortformerEncLabelModel.restore_from(diar_path) if os.path.exists(diar_path)
                           else SortformerEncLabelModel.from_pretrained(DIAR_MODEL)).eval()
        _models["load_s"] = round(time.time() - t0, 1)
    return _models


def parse_diar(raw) -> list[tuple[float, float, str]]:
    """Sortformer returns, per file, strings like '0.32 4.80 speaker_0' (or tuples)."""
    out = []
    for seg in raw:
        if isinstance(seg, str):
            a, b, spk = seg.split()[:3]
        else:
            a, b, spk = seg[:3]
        out.append((float(a), float(b), str(spk)))
    return sorted(out)


def assign(words: list[dict], turns: list[tuple[float, float, str]]) -> list[dict]:
    """Give each word the speaker whose turn overlaps it most (nearest turn if none), then merge runs."""
    segs: list[dict] = []
    for w in words:
        ws, we = float(w["start"]), float(w["end"])
        best, best_ov = None, 0.0
        for a, b, spk in turns:
            ov = min(we, b) - max(ws, a)
            if ov > best_ov:
                best, best_ov = spk, ov
        if best is None and turns:
            mid = (ws + we) / 2
            best = min(turns, key=lambda t: min(abs(mid - t[0]), abs(mid - t[1])))[2]
        spk = best or "speaker_0"
        if segs and segs[-1]["speaker"] == spk:
            segs[-1]["text"] += " " + w["word"]
            segs[-1]["end"] = we
        else:
            segs.append({"start": ws, "end": we, "speaker": spk, "text": w["word"]})
    return segs


_tts: dict = {}
# These checkpoints predate NeMo 3, which moved (and now refuses) some class paths in saved configs.
_MOVED = {
    "nemo.collections.tts.torch.g2ps.EnglishG2p": "nemo.collections.tts.g2p.models.en_us_arpabet.EnglishG2p",
    "nemo.collections.tts.torch.tts_tokenizers.": "nemo.collections.common.tokenizers.text_to_speech.tts_tokenizers.",
    "nemo.collections.tts.torch.data.TTSDataset": "nemo.collections.tts.data.dataset.TTSDataset",
}


def _restore_fixed(cls, path, dev):
    from omegaconf import OmegaConf
    cfg = cls.restore_from(path, return_config=True)
    text = OmegaConf.to_yaml(cfg)
    for old, new in _MOVED.items():
        text = text.replace(old, new)
    cfg = OmegaConf.create(text)
    OmegaConf.set_struct(cfg, False)
    for key in ("text_normalizer", "text_normalizer_call_kwargs"):
        cfg.pop(key, None)  # the normalizer needs nemo_text_processing, which we skip
    return cls.restore_from(path, override_config_path=cfg, map_location=dev).eval()



def tts_models():
    """NVIDIA FastPitch (text -> mel) + HiFi-GAN (mel -> 22 kHz audio), loaded on first use from local .nemo files."""
    if not _tts:
        _cpu_stft_patch()
        import torch
        from nemo.collections.tts.models import FastPitchModel, HifiGanModel
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        t0 = time.time()
        _tts["spec"] = _restore_fixed(FastPitchModel, os.path.join(LOCAL_DIR, "tts_en_fastpitch.nemo"), dev)
        _tts["voc"] = _restore_fixed(HifiGanModel, os.path.join(LOCAL_DIR, "tts_hifigan.nemo"), dev)
        _tts["load_s"] = round(time.time() - t0, 1)
    return _tts


@app.post("/tts")
async def tts(body: dict):
    """Text -> WAV with NVIDIA FastPitch + HiFi-GAN on the GB10."""
    import io

    import soundfile
    import torch
    from fastapi.responses import Response
    text = " ".join(str(body.get("text", "")).split())[:600]
    if not text:
        raise HTTPException(400, "no text")
    m = tts_models()
    t0 = time.time()
    # cuDNN has no kernel for some of these conv layers on the GB10 (sm_121); plain CUDA kernels work.
    with torch.no_grad(), torch.backends.cudnn.flags(enabled=False):
        try:
            tokens = m["spec"].parse(text, normalize=False)
        except TypeError:
            tokens = m["spec"].parse(text)
        spec = m["spec"].generate_spectrogram(tokens=tokens)
        audio = m["voc"].convert_spectrogram_to_audio(spec=spec)
    buf = io.BytesIO()
    soundfile.write(buf, audio.squeeze().float().cpu().numpy(), 22050, format="WAV")
    return Response(buf.getvalue(), media_type="audio/wav", headers={"X-TTS-Seconds": f"{time.time() - t0:.2f}"})


@app.get("/health")
def health():
    m = models()
    return {"ok": True, "asr": ASR_MODEL, "diarizer": DIAR_MODEL, "load_s": m["load_s"]}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "audio missing or larger than 50 MB")
    m = models()
    with tempfile.TemporaryDirectory() as d:
        src, wav = os.path.join(d, "in"), os.path.join(d, "audio.wav")
        open(src, "wb").write(data)
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000", wav], capture_output=True)
        if r.returncode:
            raise HTTPException(415, f"could not decode audio: {r.stderr.decode()[:200]}")
        t0 = time.time()
        hyp = m["asr"].transcribe([wav], timestamps=True)[0]
        t1 = time.time()
        turns = parse_diar(m["diar"].diarize(audio=[wav], batch_size=1)[0])
        t2 = time.time()
        import soundfile
        dur = soundfile.info(wav).duration
    words = [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in hyp.timestamp["word"]]
    segs = assign(words, turns)
    return {"segments": segs, "speakers": len({s["speaker"] for s in segs}), "text": hyp.text,
            "duration_s": round(dur, 1), "timings": {"asr_s": round(t1 - t0, 2), "diarization_s": round(t2 - t1, 2)},
            "models": {"asr": ASR_MODEL, "diarizer": DIAR_MODEL}}
