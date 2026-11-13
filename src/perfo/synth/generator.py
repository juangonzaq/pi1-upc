"""Synthetic corpus generator: images at 300 dpi, per-field ground truth, transcribed history CSV and masters."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from perfo.domain.fields import parse_template
from perfo.synth.content import FormContent, OperatorProfile, crew_for, generate_content
from perfo.synth.fonts import ensure_fonts
from perfo.synth.handwriting import HandStyle, make_style
from perfo.synth.masters import build_masters, write_masters

HIST_START = date(2026, 4, 1)
HIST_DAYS = 183
OPS_START = date(2026, 10, 1)
OPS_DAYS = 14
EXTRA_START = OPS_START + timedelta(days=OPS_DAYS)
EXTRA_DAYS = 60

_BLANK: dict[str, Any] = {}


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _render_worker(job: dict[str, Any]) -> dict[str, Any]:
    from perfo.synth.noise import scan  # noqa: PLC0415
    from perfo.synth.render import fill_form, render_blank  # noqa: PLC0415

    key = job["printed_font"]
    if key not in _BLANK:
        _BLANK[key] = render_blank(job["template"], job["printed_font"])
    content = FormContent(**job["content"])
    style = HandStyle(**job["style"])
    rng = random.Random(job["seed"])
    page = fill_form(_BLANK[key], job["template"], content, style, rng)
    data, params = scan(page, random.Random(job["noise_seed"]), bad=job.get("bad"))
    Path(job["image_path"]).write_bytes(data)
    return {"form_id": content.form_id, "noise": params, "flags": content.flags, "sha256": hashlib.sha256(data).hexdigest()}


def _corrupt(value: str, ftype: str, field_id: str, rng: random.Random, masters: dict[str, list[dict]], master_type: str | None) -> str:
    if ftype == "numeric":
        digits = [i for i, ch in enumerate(value) if ch.isdigit()]
        if not digits:
            return value
        i = rng.choice(digits)
        return value[:i] + str((int(value[i]) + rng.randint(1, 9)) % 10) + value[i + 1 :]
    if ftype == "time":
        h, m = map(int, value.split(":"))
        t = (h * 60 + m + rng.choice([-60, -10, 10, 60])) % 1440
        return f"{t // 60:02d}:{t % 60:02d}"
    if ftype == "date":
        return (date.fromisoformat(value) + timedelta(days=rng.choice([-1, 1]))).isoformat()
    if ftype == "master_ref" and master_type:
        return rng.choice([x["codigo"] for x in masters[master_type] if x["codigo"] != value])
    if ftype == "code":
        if field_id.startswith("header.shift"):
            return "N" if value == "D" else "D"
        if field_id.startswith("header.crew"):
            return rng.choice([c for c in "ABC" if c != value])
        return str(int(value) + rng.choice([-1, 1])) if value.isdigit() else value
    return value


def _operator_profiles(
    seed: int, operators: list[dict[str, Any]], new_ops: list[dict[str, Any]], reserved: list[str], regular: list[str],
) -> tuple[dict[str, OperatorProfile], dict[str, HandStyle]]:
    """Handwriting style and writing habits per operator (deterministic from the corpus seed)."""
    profiles: dict[str, OperatorProfile] = {}
    styles: dict[str, HandStyle] = {}
    for i, op in enumerate(operators):
        orng = random.Random(_seed(seed, "operator", op["codigo"]))
        font_pool = reserved if op in new_ops else [regular[i % len(regular)]]
        styles[op["codigo"]] = make_style(orng, font_pool)
        profiles[op["codigo"]] = OperatorProfile(
            code=op["codigo"], style_id=i, time_style=orng.choice(["24h", "24h", "12h", "12h_suffix"]),
            decimal_comma=orng.random() < 0.3, date_style=orng.randrange(4), labor_upper=orng.random() < 0.7,
        )
    return profiles, styles


def generate_corpus(cfg: dict[str, Any], template_def: dict[str, Any], out_dir: Path, fonts_dir: Path, seed: int) -> dict[str, Any]:
    """Generates the full synthetic corpus and returns its manifest."""
    sc = cfg
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "images" / "historical").mkdir(parents=True, exist_ok=True)
    (out_dir / "images" / "operational").mkdir(parents=True, exist_ok=True)
    (out_dir / "gt").mkdir(parents=True, exist_ok=True)

    fonts = ensure_fonts(fonts_dir)
    hand = fonts["handwriting"]
    n_ops, n_new = int(sc["operators"]), int(sc["new_operators"])
    reserved = hand[-n_new:] if len(hand) > n_new + 3 else hand
    regular = hand[: len(hand) - n_new] if len(hand) > n_new + 3 else hand

    masters = build_masters(rng, int(sc["labores"]), int(sc["equipment"]), n_ops + n_new)
    master_paths = write_masters(masters, out_dir / "masters")
    operators = [p for p in masters["personal"] if p["rol"] == "OPERADOR"]
    hist_ops, new_ops = operators[:n_ops], operators[n_ops:]
    profiles, styles = _operator_profiles(seed, operators, new_ops, reserved, regular)

    equipment = masters["equipos"]
    template = parse_template(template_def)
    types = {f.id: (f.type.value, f.master_type) for f in template.fields}

    def plan(batch: str, n: int, start: date, days: int, ops_pool: list[dict]) -> list[dict[str, Any]]:
        keys = set()
        forms = []
        ops_cycle: list[dict] = []
        while len(forms) < n:
            d_idx = rng.randrange(days)
            shift = rng.choice("DN")
            eq = rng.choice(equipment)
            k = (d_idx, shift, eq["codigo"])
            if k in keys:
                continue
            keys.add(k)
            if not ops_cycle:
                ops_cycle = ops_pool[:]
                rng.shuffle(ops_cycle)
            forms.append({"day": d_idx, "date": start + timedelta(days=d_idx), "shift": shift, "equipment": eq, "operator": ops_cycle.pop()})
        return forms

    n_hist, n_op = int(sc["historical_forms"]), int(sc["operational_forms"])
    hist_plan = plan("historical", n_hist, HIST_START, HIST_DAYS, hist_ops)
    mix = new_ops * 3 + hist_ops
    ops_plan = plan("operational", n_op, OPS_START, OPS_DAYS, mix)

    bad_hist = set(rng.sample(range(n_hist), k=min(n_hist, int(sc.get("bad_scans_historical", 0)))))
    bad_ops = set(rng.sample(range(n_op), k=min(n_op, int(sc.get("bad_scans_operational", 0)))))

    jobs, forms_meta = [], []
    printed = fonts["printed"][0]
    for batch, plan_list, prefix, bad_set in (("historical", hist_plan, "H", bad_hist), ("operational", ops_plan, "O", bad_ops)):
        for i, p in enumerate(plan_list):
            form_id = f"{prefix}{i + 1:04d}"
            frng = random.Random(_seed(seed, form_id))
            content = generate_content(form_id, batch, frng, p["date"], (p["date"] - HIST_START).days, p["shift"], p["equipment"],
                                       p["operator"], profiles[p["operator"]["codigo"]], masters, sc)
            bad = None
            if i in bad_set:
                bad = "low_res" if (i % 2 == 0) else "cut"
            image_path = out_dir / "images" / batch / f"{form_id}.jpg"
            content_dict = asdict(content)
            jobs.append({
                "template": template_def, "printed_font": printed, "content": content_dict,
                "style": asdict(styles[p["operator"]["codigo"]]), "seed": _seed(seed, form_id, "render"),
                "noise_seed": _seed(seed, form_id, "noise"), "image_path": str(image_path), "bad": bad,
            })
            forms_meta.append({"form_id": form_id, "batch": batch, "image": str(image_path.relative_to(out_dir)), "bad_scan": bad,
                               "true_key": f"{content.date}|{content.crew}|{content.equipment}", "operator": content.operator,
                               "new_operator": p["operator"] in new_ops, "content": content})

    # a re-scan of one operational form (same business key, different file) -> DUPLICADO_PENDIENTE
    rescan = None
    if ops_plan:
        src = next(j for j, m in zip(jobs, forms_meta, strict=True) if m["batch"] == "operational" and not m["bad_scan"])
        rescan_path = Path(src["image_path"]).with_name(Path(src["image_path"]).stem + "_reescaneo.jpg")
        rjob = {**src, "noise_seed": src["noise_seed"] + 1, "image_path": str(rescan_path), "bad": None}
        jobs.append(rjob)
        rescan = {"form_id": src["content"]["form_id"], "image": str(rescan_path.relative_to(out_dir))}

    workers = int(sc.get("workers", 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_render_worker, jobs, chunksize=2))
    by_id: dict[str, dict] = {}
    for r in results:
        by_id.setdefault(r["form_id"], r)

    # ground truth per form
    manifest_forms = []
    for m in forms_meta:
        c: FormContent = m.pop("content")
        res = by_id[c.form_id]
        gt = _gt_payload(c, res, styles[c.operator], m["bad_scan"])
        (out_dir / "gt" / f"{c.form_id}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1))
        m["gt"] = f"gt/{c.form_id}.json"
        m["image_sha256"] = res["sha256"]
        m["metadata_key"] = m["true_key"]
        manifest_forms.append(m)

    # metadata errors at scanning time (file naming): key that matches no record
    hist_ids = [m for m in manifest_forms if m["batch"] == "historical"]
    all_keys = {m["true_key"] for m in hist_ids}
    for m in rng.sample(hist_ids, k=int(round(len(hist_ids) * float(sc.get("metadata_error_rate", 0))))):
        d, crew, eq = m["true_key"].split("|")
        for alt in "ABC":
            wrong = f"{d}|{alt}|{eq}"
            if alt != crew and wrong not in all_keys:
                m["metadata_key"] = wrong
                m["metadata_error"] = True
                break

    # transcribed history (simulates the current web system export)
    history_path = out_dir / "history" / "registro_historico.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    stats = {"records": 0, "missing": [], "duplicated": [], "field_errors": 0, "fields": 0}
    rows: list[list[str]] = []
    rec_id = 0
    for m in hist_ids:
        gt = json.loads((out_dir / m["gt"]).read_text())
        if rng.random() < float(sc.get("history_missing_rate", 0)):
            stats["missing"].append(m["form_id"])
            continue
        copies = 2 if rng.random() < float(sc.get("history_duplicate_rate", 0)) else 1
        if copies == 2:
            stats["duplicated"].append(m["form_id"])
        for copy_i in range(copies):
            rec_id += 1
            stats["records"] += 1
            for fid, spec in gt["fields"].items():
                if spec["value"] is None:
                    continue
                val = spec["value"]
                ftype, mtype = types[fid]
                err_rate = float(sc.get("history_error_rate", 0)) + (0.05 if copy_i == 1 else 0)
                if rng.random() < err_rate:
                    val = _corrupt(val, ftype, fid, rng, masters, mtype)
                    stats["field_errors"] += 1
                stats["fields"] += 1
                rows.append([f"R{rec_id:06d}", gt["date"], gt["crew"], gt["equipment"], fid, val])
    with history_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["registro_id", "fecha", "guardia", "equipo", "campo", "valor"])
        w.writerows(rows)

    manifest = {
        "seed": seed,
        "fonts_source": fonts["source"][0],
        "fonts": [Path(f).stem for f in hand],
        "reserved_fonts_new_operators": [Path(f).stem for f in reserved],
        "masters": {k: str(Path(v).relative_to(out_dir)) for k, v in master_paths.items()},
        "history_csv": str(history_path.relative_to(out_dir)),
        "history_stats": stats,
        "operators": {"historical": [o["codigo"] for o in hist_ops], "new": [o["codigo"] for o in new_ops]},
        "supervisors": [p["nombre"] for p in masters["personal"] if p["rol"] != "OPERADOR"],
        "forms": manifest_forms,
        "rescan": rescan,
        "config": {k: v for k, v in sc.items()},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def _gt_payload(c: FormContent, render: dict[str, Any], style: HandStyle, bad: str | None) -> dict[str, Any]:
    return {
        "form_id": c.form_id, "batch": c.batch, "date": c.date, "shift": c.shift, "crew": c.crew, "equipment": c.equipment,
        "operator": c.operator, "fields": c.fields, "extra_text": c.extra_text, "diagonal": c.diagonal,
        "signatures": c.signatures, "flags": render["flags"], "noise": render["noise"], "bad_scan": bad,
        "style": asdict(style) | {"font": Path(style.font_path).stem},
    }


def generate_extra_forms(out_dir: Path, template_def: dict[str, Any], fonts_dir: Path, n: int, batch: str = "demo") -> list[dict[str, Any]]:
    """Adds `n` new forms to an existing corpus (same masters, operators and handwriting), unseen by every model.

    Used by the web demo to test recognition on forms with known ground truth. Dates fall after the operational
    window and business keys never collide with the corpus, so the forms are never protected by the frozen split.
    """
    man_path = out_dir / "manifest.json"
    man = json.loads(man_path.read_text())
    seed, sc = int(man["seed"]), man["config"]
    fonts = ensure_fonts(fonts_dir)
    by_stem = {Path(p).stem: p for p in fonts["handwriting"]}
    hand = [by_stem[s] for s in man["fonts"] if s in by_stem]
    reserved_stems = set(man["reserved_fonts_new_operators"])
    reserved = [p for p in hand if Path(p).stem in reserved_stems]
    regular = [p for p in hand if Path(p).stem not in reserved_stems] or hand

    masters = {}
    for k in ("labores", "equipos", "personal", "codigos"):
        with (out_dir / man["masters"][k]).open(encoding="utf-8", newline="") as fh:
            masters[k] = list(csv.DictReader(fh))
    operators = [p for p in masters["personal"] if p["rol"] == "OPERADOR"]
    n_ops = int(sc["operators"])
    hist_ops, new_ops = operators[:n_ops], operators[n_ops:]
    profiles, styles = _operator_profiles(seed, operators, new_ops, reserved, regular)

    start = sum(1 for f in man["forms"] if f["batch"] == batch)
    rng = random.Random(_seed(seed, "extra", batch, start))
    used = {f["true_key"] for f in man["forms"]}
    mix = new_ops * 3 + hist_ops
    (out_dir / "images" / batch).mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(n):
        while True:
            d = EXTRA_START + timedelta(days=rng.randrange(EXTRA_DAYS))
            shift = rng.choice("DN")
            eq = rng.choice(masters["equipos"])
            if f"{d.isoformat()}|{crew_for((d - HIST_START).days, shift)}|{eq['codigo']}" not in used:
                break
        op = rng.choice(mix)
        form_id = f"{batch[0].upper()}{start + i + 1:04d}"
        content = generate_content(form_id, batch, random.Random(_seed(seed, form_id)), d, (d - HIST_START).days, shift, eq, op,
                                   profiles[op["codigo"]], masters, sc)
        image_path = out_dir / "images" / batch / f"{form_id}.jpg"
        res = _render_worker({
            "template": template_def, "printed_font": fonts["printed"][0], "content": asdict(content), "style": asdict(styles[op["codigo"]]),
            "seed": _seed(seed, form_id, "render"), "noise_seed": _seed(seed, form_id, "noise"), "image_path": str(image_path), "bad": None,
        })
        (out_dir / "gt" / f"{form_id}.json").write_text(json.dumps(_gt_payload(content, res, styles[op["codigo"]], None), ensure_ascii=False, indent=1))
        key = f"{content.date}|{content.crew}|{content.equipment}"
        used.add(key)
        entries.append({"form_id": form_id, "batch": batch, "image": str(image_path.relative_to(out_dir)), "bad_scan": None, "true_key": key,
                        "operator": content.operator, "new_operator": op in new_ops, "gt": f"gt/{form_id}.json", "image_sha256": res["sha256"],
                        "metadata_key": key})
    man["forms"].extend(entries)
    tmp = man_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1))
    tmp.replace(man_path)
    return entries
