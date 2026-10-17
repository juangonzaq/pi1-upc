"""RecognitionPort adapter with Hugging Face TrOCR.

Field confidence comes from per-token probabilities of the generation (raw logits per step):
  - mean_logprob: exp(mean_t log p_T(y_t))  (geometric mean, length-independent)
  - min_prob:     exp(min_t log p_T(y_t))
where p_T is the softmax at temperature T. A calibration profile provides T per field type.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402
from PIL import Image  # noqa: E402

from perfo.ports.recognition import RecognitionOutput, RecognitionPort, RecognizerUnavailable  # noqa: E402

AGGREGATIONS = ("mean_logprob", "min_prob")


@dataclass
class TokenStats:
    text: str
    n_tokens: int
    # conf[aggregation][T] for each temperature of the requested grid
    conf: dict[str, dict[float, float]]


def load_trocr(path_or_id: str, device: str) -> tuple[Any, Any]:
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # noqa: PLC0415
    from transformers.utils import logging as hf_logging  # noqa: PLC0415

    hf_logging.set_verbosity_error()
    processor = TrOCRProcessor.from_pretrained(path_or_id)
    model = VisionEncoderDecoderModel.from_pretrained(path_or_id)
    gc = model.generation_config
    model.config.decoder_start_token_id = gc.decoder_start_token_id if gc.decoder_start_token_id is not None else processor.tokenizer.eos_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.eos_token_id = processor.tokenizer.eos_token_id
    gc.pad_token_id = processor.tokenizer.pad_token_id
    gc.eos_token_id = processor.tokenizer.eos_token_id
    gc.decoder_start_token_id = model.config.decoder_start_token_id
    model.to(device)
    model.eval()
    return processor, model


class TrocrRecognizer(RecognitionPort):
    def __init__(
        self,
        path_or_id: str,
        model_version: str,
        device: str = "cpu",
        aggregation: str = "mean_logprob",
        calibration: dict[str, Any] | None = None,
        batch_size: int = 32,
        max_new_tokens: int = 24,
    ):
        if aggregation not in AGGREGATIONS:
            raise ValueError(f"Agregación desconocida: {aggregation}")
        self.path_or_id = path_or_id
        self._version = model_version
        self.device = device
        self.aggregation = aggregation
        self.calibration = calibration
        self.batch_size = batch_size
        self.max_new_tokens = max_new_tokens
        self._processor: Any = None
        self._model: Any = None

    @property
    def model_version(self) -> str:
        return self._version

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            self._processor, self._model = load_trocr(self.path_or_id, self.device)
        except Exception as exc:  # noqa: BLE001
            raise RecognizerUnavailable(f"No se pudo cargar el modelo {self.path_or_id}: {exc}") from exc

    @property
    def model(self) -> Any:
        self._ensure_loaded()
        return self._model

    @property
    def processor(self) -> Any:
        self._ensure_loaded()
        return self._processor

    # ------------------------------------------------------------------ inference core
    @torch.no_grad()
    def infer(self, images: list[Image.Image], temperatures: list[float] | None = None) -> list[TokenStats]:
        self._ensure_loaded()
        temps = sorted(set([1.0, *(temperatures or [])]))
        out: list[TokenStats] = []
        eos = self._processor.tokenizer.eos_token_id
        for i in range(0, len(images), self.batch_size):
            batch = images[i : i + self.batch_size]
            try:
                pv = self._processor(images=batch, return_tensors="pt").pixel_values.to(self.device)
                gen = self._model.generate(
                    pv, max_new_tokens=self.max_new_tokens, num_beams=1, do_sample=False,
                    output_logits=True, return_dict_in_generate=True, use_cache=self.device != "mps",  # transformers 5.20: KV cache fails on MPS
                )
            except RuntimeError as exc:
                raise RecognizerUnavailable(f"Fallo de inferencia en {self.device}: {exc}") from exc
            seqs = gen.sequences  # (B, S+1) starting with decoder_start
            steps = len(gen.logits)
            B = seqs.shape[0]
            T = torch.tensor(temps, dtype=torch.float32)
            sums = torch.zeros(len(temps), B)
            mins = torch.full((len(temps), B), float("inf"))
            count = torch.zeros(B)
            finished = torch.zeros(B, dtype=torch.bool)
            for t in range(steps):
                z = gen.logits[t].float().cpu()  # (B, V)
                y = seqs[:, t + 1].cpu()
                valid = ~finished
                zy = z.gather(1, y[:, None]).squeeze(1)  # (B,)
                lse = torch.stack([torch.logsumexp(z / tv, dim=1) for tv in temps])  # (nT, B)
                logp = zy[None, :] / T[:, None] - lse
                sums += torch.where(valid[None, :], logp, torch.zeros_like(logp))
                mins = torch.where(valid[None, :], torch.minimum(mins, logp), mins)
                count += valid.float()
                finished |= y == eos
            texts = self._processor.batch_decode(seqs, skip_special_tokens=True)
            for b in range(B):
                n = max(1.0, float(count[b]))
                conf = {
                    "mean_logprob": {tv: float(torch.exp(sums[k, b] / n)) for k, tv in enumerate(temps)},
                    "min_prob": {tv: float(torch.exp(mins[k, b])) if torch.isfinite(mins[k, b]) else 0.0 for k, tv in enumerate(temps)},
                }
                out.append(TokenStats(texts[b].strip(), int(count[b]), conf))
        return out

    # ------------------------------------------------------------------ port
    def recognize(self, field_image: bytes, field_type: str) -> RecognitionOutput:
        return self.recognize_batch([(field_image, field_type)])[0]

    def recognize_batch(self, items: list[tuple[bytes, str]]) -> list[RecognitionOutput]:
        if not items:
            return []
        images = [Image.open(io.BytesIO(b)).convert("RGB") for b, _ in items]
        temps_by_type = (self.calibration or {}).get("temperatures", {})
        needed = sorted({float(t) for t in temps_by_type.values() if t is not None})
        t0 = time.perf_counter()
        stats = self.infer(images, needed)
        latency = (time.perf_counter() - t0) * 1000 / len(items)
        outs = []
        for (_, ftype), st in zip(items, stats, strict=True):
            raw = st.conf[self.aggregation][1.0]
            T = temps_by_type.get(ftype)
            if T is not None:
                score, reliable = st.conf[self.aggregation][float(T)], True
            else:
                score, reliable = raw, False  # uncalibrated: never auto-accepted
            if not st.text:
                reliable = False
            outs.append(RecognitionOutput(st.text, score, self._version, raw, reliable, (self.calibration or {}).get("id"), latency))
        return outs
