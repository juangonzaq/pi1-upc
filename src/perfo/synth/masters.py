"""Synthetic masters (labores, equipos, personal, códigos) and handwritten variants of their entries."""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Any

from perfo.synth.layout import ACTIVITY_CODES, DELAY_CODES, FAILURE_CODES

LABOR_TYPES = [
    ("TAJO", ["TJ", "Tj", "Tajo", "TAJO", "TJ."], 0.45),
    ("NIVEL", ["NV", "Nv.", "Nivel", "NV.", "NIV"], 0.12),
    ("GALERIA", ["GAL", "Gal", "GL", "Galeria", "GAL."], 0.12),
    ("RAMPA", ["RP", "Rp", "Rampa", "RA"], 0.08),
    ("CRUCERO", ["CX", "Cx", "XC", "Crucero"], 0.08),
    ("SUBNIVEL", ["SN", "Sn", "Subnivel"], 0.06),
    ("CHIMENEA", ["CH", "Ch", "Chimenea"], 0.04),
    ("BYPASS", ["BP", "Bp", "Bypass"], 0.03),
    ("VENTANA", ["VN", "Vn", "Ventana"], 0.02),
]
CARDINALS = {"NORTE": ["N", "N", "Nte", "NORTE", "Norte"], "SUR": ["S", "S", "Sur", "SUR"], "ESTE": ["E", "Este", "E"], "OESTE": ["W", "O", "Oeste"]}

EQUIPMENT_MODELS = [("SIMBA S7D", ["SIMBA", "S7D", "Simba S7D", "S7"]), ("SIMBA M4C", ["SIMBA", "M4C", "Simba M4C"]), ("RAPTOR 44", ["RAPTOR", "R44", "Raptor"])]

FIRST_NAMES = [
    "JUAN", "CARLOS", "LUIS", "JOSE", "MIGUEL", "PEDRO", "JORGE", "VICTOR", "MARCO", "CESAR", "RAUL", "EDWIN",
    "WILBER", "ROBERTO", "FREDY", "ALEX", "HUGO", "ELMER", "DAVID", "RONALD", "NESTOR", "WALTER", "ISAAC", "RUBEN",
]
SURNAMES = [
    "QUISPE", "MAMANI", "HUAMAN", "FLORES", "CONDORI", "CHOQUE", "TORRES", "RAMOS", "MENDOZA", "CRUZ", "GUTIERREZ",
    "VARGAS", "ROJAS", "CASTILLO", "SALAZAR", "PAUCAR", "HUANCA", "APAZA", "TICONA", "CCAHUANA", "YUPANQUI", "LAURA",
]


def build_masters(rng: random.Random, n_labores: int, n_equipment: int, n_operators: int, n_supervisors: int = 6) -> dict[str, list[dict[str, Any]]]:
    labores: list[dict[str, Any]] = []
    seen: set[str] = set()
    weights = [w for _, _, w in LABOR_TYPES]
    while len(labores) < n_labores:
        ltype, _, _ = rng.choices(LABOR_TYPES, weights=weights)[0]
        number = rng.choice(range(3900, 4520, 10))
        cardinal = rng.choice(list(CARDINALS)) if ltype in ("TAJO", "GALERIA", "CRUCERO", "SUBNIVEL") and rng.random() < 0.8 else ""
        desc = f"{ltype} {number}" + (f" {cardinal}" if cardinal else "")
        if desc in seen:
            continue
        seen.add(desc)
        labores.append({"codigo": f"LB-{len(labores) + 1:04d}", "descripcion": desc, "tipo": ltype})
    # guarantee enough stopes
    if sum(1 for l in labores if l["tipo"] == "TAJO") < 4:
        for l in labores[:4]:
            l["tipo"] = "TAJO"
            l["descripcion"] = "TAJO " + l["descripcion"].split(" ", 1)[1]

    equipos = []
    for i in range(n_equipment):
        model, _ = EQUIPMENT_MODELS[i % len(EQUIPMENT_MODELS)]
        equipos.append({"codigo": f"PL-{i + 1:02d}", "descripcion": f"{model} N° {i + 1:02d}", "modelo": model})

    names: set[str] = set()
    personal = []
    total = n_operators + n_supervisors
    while len(personal) < total:
        first = rng.choice(FIRST_NAMES)
        s1, s2 = rng.sample(SURNAMES, 2)
        full = f"{s1} {s2}, {first}"
        if full in names:
            continue
        names.add(full)
        role = "OPERADOR" if len(personal) < n_operators else "JEFE DE GUARDIA"
        personal.append({"codigo": f"P{len(personal) + 1:04d}", "nombre": full, "rol": role})

    codigos = (
        [{"tipo": "actividad", "codigo": k, "descripcion": v} for k, v in ACTIVITY_CODES.items()]
        + [{"tipo": "demora", "codigo": k, "descripcion": v} for k, v in DELAY_CODES.items()]
        + [{"tipo": "falla", "codigo": k, "descripcion": v} for k, v in FAILURE_CODES.items()]
    )
    return {"labores": labores, "equipos": equipos, "personal": personal, "codigos": codigos}


def write_masters(masters: dict[str, list[dict[str, Any]]], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for mtype, rows in masters.items():
        path = out_dir / f"{mtype}.csv"
        cols = list(rows[0].keys())
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        paths[mtype] = str(path)
    # an invalid export (missing column + duplicated code) to exercise rejection
    bad = out_dir / "labores_invalido.csv"
    with bad.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo", "detalle"])
        w.writerow(["LB-0001", "TAJO DUPLICADO"])
        w.writerow(["LB-0001", "TAJO DUPLICADO"])
    paths["labores_invalido"] = str(bad)
    return paths


# ------------------------------------------------------------------ handwritten variants


def labor_variant(desc: str, rng: random.Random, upper: bool = True) -> str:
    parts = desc.split()
    ltype, number = parts[0], parts[1]
    cardinal = parts[2] if len(parts) > 2 else ""
    abbrs = next(a for t, a, _ in LABOR_TYPES if t == ltype)
    head = rng.choice(abbrs)
    sep = rng.choice([" ", " ", " ", "-"])
    text = f"{head}{sep}{number}"
    if cardinal:
        c = rng.choice(CARDINALS[cardinal])
        text += rng.choice(["", " ", " "]) + c if len(c) == 1 else " " + c
    if upper or rng.random() < 0.5:
        text = text.upper()
    return text


def equipment_variant(item: dict[str, Any], rng: random.Random) -> str:
    num = item["codigo"].split("-")[1]
    model = item["modelo"]
    shorts = next(s for m, s in EQUIPMENT_MODELS if m == model)
    choice = rng.random()
    if choice < 0.25:
        return item["codigo"] if rng.random() < 0.6 else item["codigo"].replace("-", " ")
    short = rng.choice(shorts)
    n = num if rng.random() < 0.7 else str(int(num))
    return f"{short}{rng.choice([' ', '-', ' N° '])}{n}".upper() if rng.random() < 0.8 else f"{short} {n}"


def person_variant(item: dict[str, Any], rng: random.Random) -> str:
    surnames, given = [p.strip() for p in item["nombre"].split(",")]
    s1, s2 = surnames.split()[:2]
    g = given.split()[0]
    options = [
        f"{g[0]}. {s1}",
        f"{s1} {g[0]}.",
        f"{g.title()} {s1.title()}",
        f"{g[0]}. {s1} {s2[0]}.",
        f"{s1} {s2}",
        f"{g} {s1}",
    ]
    text = rng.choice(options)
    return text.upper() if rng.random() < 0.6 else text
