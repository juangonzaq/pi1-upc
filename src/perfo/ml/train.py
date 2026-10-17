"""TrOCR fine-tuning (full by default; LoRA behind a flag) with early stopping on the model-selection half."""

from __future__ import annotations

import copy
import io
import json
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageFilter

from perfo.adapters.trocr.recognizer import TrocrRecognizer, load_trocr
from perfo.ml.metrics import cer, literal


class TrainingFailed(Exception):
    pass


@dataclass
class TrainSample:
    object_key: str
    label: str


@dataclass
class TrainResult:
    out_dir: str
    best_step: int
    best_select_cer: float
    initial_select_cer: float
    steps: int
    seconds: float
    history: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)


def _augment(img: Image.Image, rng: random.Random) -> Image.Image:
    if rng.random() < 0.5:
        img = img.rotate(rng.uniform(-2.5, 2.5), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    if rng.random() < 0.3:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    if rng.random() < 0.4:
        w, h = img.size
        s = rng.uniform(0.85, 1.15)
        img = img.resize((max(8, int(w * s)), max(8, int(h * rng.uniform(0.9, 1.1)))), Image.Resampling.BICUBIC)
    return img


def train_trocr(
    storage: Any,
    init_path: str,
    train: list[TrainSample],
    select: list[TrainSample],
    out_dir: str | Path,
    cfg: dict[str, Any],
    device: str,
    seed: int,
    log: Callable[[str], None] = print,
) -> TrainResult:
    if not train:
        raise TrainingFailed("No hay ejemplos de entrenamiento")
    set_seed(seed)
    t_start = time.perf_counter()
    try:
        processor, model = load_trocr(init_path, device)
    except Exception as exc:  # noqa: BLE001
        raise TrainingFailed(f"No se pudo cargar el modelo inicial {init_path}: {exc}") from exc

    use_lora = bool(cfg.get("lora", False))
    if use_lora:
        try:
            from peft import LoraConfig, get_peft_model  # noqa: PLC0415
        except ImportError as exc:
            raise TrainingFailed("LoRA requiere el extra 'lora' (pip install -e '.[lora]')") from exc
        lcfg = LoraConfig(r=int(cfg.get("lora_r", 16)), lora_alpha=int(cfg.get("lora_alpha", 32)), lora_dropout=0.05,
                          target_modules=["q_proj", "v_proj", "query", "value"])
        model = get_peft_model(model, lcfg)

    tok = processor.tokenizer
    eos = tok.eos_token_id
    images = {s.object_key: Image.open(io.BytesIO(storage.get(s.object_key))).convert("RGB") for s in train + select}
    sel_imgs = [images[s.object_key] for s in select]
    sel_refs = [literal(s.label) for s in select]

    evaluator = TrocrRecognizer(init_path, "training", device, batch_size=int(cfg.get("eval_batch_size", 32)))
    evaluator._processor = processor  # share the model being trained

    def eval_select() -> float:
        if not select:
            return float("nan")
        model.eval()
        evaluator._model = model.base_model.model if use_lora else model
        texts = [st.text for st in evaluator.infer(sel_imgs)]
        model.train()
        return cer(sel_refs, [literal(t) for t in texts])

    bs = int(cfg.get("batch_size", 16))
    max_steps = int(cfg.get("max_steps", 1000))
    eval_every = int(cfg.get("eval_every", 100))
    patience = int(cfg.get("patience", 3))
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=float(cfg.get("lr", 5e-5)), weight_decay=float(cfg.get("weight_decay", 0.01)))
    warmup = max(1, int(max_steps * float(cfg.get("warmup_ratio", 0.05))))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warmup) * max(0.05, 1 - s / max_steps))
    augment = bool(cfg.get("augment", True))
    rng = random.Random(seed)

    initial = eval_select()
    log(f"    CER inicial en selección: {initial:.4f}")
    best_cer, best_step = initial, 0
    best_state = {k: v.detach().to("cpu", copy=True) for k, v in model.state_dict().items()}
    history = [{"step": 0, "select_cer": initial}]
    bad_evals = 0
    step = 0
    order: list[int] = []
    model.train()
    losses: list[float] = []
    try:
        while step < max_steps:
            if not order:
                order = list(range(len(train)))
                rng.shuffle(order)
            idx = [order.pop() for _ in range(min(bs, len(order)))]
            batch = [train[i] for i in idx]
            imgs = [_augment(images[s.object_key], rng) if augment else images[s.object_key] for s in batch]
            pv = processor(images=imgs, return_tensors="pt").pixel_values.to(device)
            ids = [tok(literal(s.label), add_special_tokens=False).input_ids[:30] + [eos] for s in batch]
            width = max(len(x) for x in ids)
            labels = torch.full((len(ids), width), -100, dtype=torch.long)
            for j, x in enumerate(ids):
                labels[j, : len(x)] = torch.tensor(x)
            loss = model(pixel_values=pv, labels=labels.to(device)).loss
            if not torch.isfinite(loss):
                raise TrainingFailed(f"Pérdida no finita en el paso {step}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            losses.append(float(loss))
            step += 1
            if step % eval_every == 0 or step == max_steps:
                sc = eval_select()
                history.append({"step": step, "select_cer": sc, "train_loss": float(np.mean(losses[-eval_every:]))})
                log(f"    paso {step}: pérdida {np.mean(losses[-eval_every:]):.3f} · CER selección {sc:.4f}")
                if sc < best_cer - 1e-6:
                    best_cer, best_step, bad_evals = sc, step, 0
                    best_state = {k: v.detach().to("cpu", copy=True) for k, v in model.state_dict().items()}
                else:
                    bad_evals += 1
                    if bad_evals >= patience:
                        log(f"    early stopping en el paso {step} (mejor paso {best_step})")
                        break
    except TrainingFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TrainingFailed(f"Fallo durante el entrenamiento: {exc}") from exc

    model.load_state_dict(best_state)
    if use_lora:
        model = model.merge_and_unload()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.to("cpu").save_pretrained(out, safe_serialization=True)
    processor.save_pretrained(out)
    conf = {"seed": seed, "init": init_path, "steps": step, "best_step": best_step, "lora": use_lora, "train_size": len(train),
            "select_size": len(select), "device": device, **{k: v for k, v in cfg.items() if not isinstance(v, (dict, list))}}
    (out / "training_config.json").write_text(json.dumps(conf, indent=2, ensure_ascii=False))
    del best_state
    res = TrainResult(str(out), best_step, best_cer, initial, step, time.perf_counter() - t_start, history, conf)
    if math.isnan(best_cer):
        res.best_select_cer = float("nan")
    return copy.copy(res)
