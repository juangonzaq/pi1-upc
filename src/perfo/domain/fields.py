"""Field and template definitions (pure domain, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldType(StrEnum):
    NUMERIC = "numeric"
    CODE = "code"
    TIME = "time"
    DATE = "date"
    FREE_TEXT = "free_text"
    MASTER_REF = "master_ref"


REQUIRED_ALWAYS = "always"
REQUIRED_IF_ROW = "if_row"
REQUIRED_NO = "no"


@dataclass(frozen=True)
class FieldSpec:
    """A field instance of the template (a header box or a single table cell)."""

    id: str  # instance id, e.g. "header.date" or "activities.r03.code"
    key: str  # concept id, e.g. "header.date" or "activities.code"
    block: str
    label: str
    type: FieldType
    box: tuple[float, float, float, float]  # normalized x0, y0, x1, y1
    required: str = REQUIRED_NO
    priority: bool = True
    subtype: str | None = None  # integer | decimal for numeric
    master_type: str | None = None  # labores | equipos | personal
    code_set: str | None = None  # name of a controlled code set
    min_value: float | None = None
    max_value: float | None = None
    table: str | None = None
    row: int | None = None  # 1-based row for table cells
    col: str | None = None
    signature: bool = False

    @property
    def is_cell(self) -> bool:
        return self.table is not None


@dataclass(frozen=True)
class AlignmentSpec:
    tolerance_px: float = 12.0
    min_inliers: int = 40
    max_reproj_error_px: float = 4.0
    min_dpi: float = 200.0
    min_sharpness: float = 15.0


@dataclass(frozen=True)
class Template:
    template_id: str
    version: str
    valid_from: str
    width: int
    height: int
    dpi: int
    fields: tuple[FieldSpec, ...]
    alignment: AlignmentSpec
    code_sets: dict[str, dict[str, Any]]
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    def field(self, field_id: str) -> FieldSpec:
        for spec in self.fields:
            if spec.id == field_id:
                return spec
        raise KeyError(field_id)

    def prioritized(self) -> list[FieldSpec]:
        return [f for f in self.fields if f.priority and not f.signature]

    def signatures(self) -> list[FieldSpec]:
        return [f for f in self.fields if f.signature]

    def box_px(self, spec: FieldSpec) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = spec.box
        return (
            round(x0 * self.width),
            round(y0 * self.height),
            round(x1 * self.width),
            round(y1 * self.height),
        )

    def field_keys(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.key not in seen and not f.signature:
                seen.append(f.key)
        return seen


def cell_id(table: str, row: int, col: str) -> str:
    return f"{table}.r{row:02d}.{col}"


def parse_template(data: dict[str, Any]) -> Template:
    """Builds a Template from its JSON definition, expanding tables into cells."""
    ref = data["reference"]
    width, height = int(ref["width"]), int(ref["height"])
    fields: list[FieldSpec] = []

    for f in data.get("fields", []):
        fields.append(
            FieldSpec(
                id=f["id"],
                key=f["id"],
                block=f["block"],
                label=f.get("label", f["id"]),
                type=FieldType(f["type"]),
                box=tuple(f["box"]),  # type: ignore[arg-type]
                required=f.get("required", REQUIRED_NO),
                priority=bool(f.get("priority", True)),
                subtype=f.get("subtype"),
                master_type=f.get("master_type"),
                code_set=f.get("code_set"),
                min_value=f.get("min"),
                max_value=f.get("max"),
            )
        )

    for t in data.get("tables", []):
        x0, y0, x1, _ = t["box"]
        header_h = t["header_height"]
        row_h = t["row_height"]
        for r in range(1, int(t["rows"]) + 1):
            ry0 = y0 + header_h + (r - 1) * row_h
            ry1 = ry0 + row_h
            for c in t["columns"]:
                fields.append(
                    FieldSpec(
                        id=cell_id(t["id"], r, c["id"]),
                        key=f"{t['id']}.{c['id']}",
                        block=t["block"],
                        label=c.get("label", c["id"]),
                        type=FieldType(c["type"]),
                        box=(c["x0"], ry0, c["x1"], ry1),
                        required=c.get("required", REQUIRED_NO),
                        priority=bool(c.get("priority", True)),
                        subtype=c.get("subtype"),
                        master_type=c.get("master_type"),
                        code_set=c.get("code_set"),
                        min_value=c.get("min"),
                        max_value=c.get("max"),
                        table=t["id"],
                        row=r,
                        col=c["id"],
                    )
                )
        del x0, x1

    for s in data.get("signatures", []):
        fields.append(
            FieldSpec(
                id=s["id"],
                key=s["id"],
                block="signatures",
                label=s.get("label", s["id"]),
                type=FieldType.FREE_TEXT,
                box=tuple(s["box"]),  # type: ignore[arg-type]
                priority=False,
                signature=True,
            )
        )

    al = data.get("alignment", {})
    alignment = AlignmentSpec(
        tolerance_px=float(al.get("tolerance_px", 12.0)),
        min_inliers=int(al.get("min_inliers", 40)),
        max_reproj_error_px=float(al.get("max_reproj_error_px", 4.0)),
        min_dpi=float(al.get("min_dpi", 200.0)),
        min_sharpness=float(al.get("min_sharpness", 15.0)),
    )
    return Template(
        template_id=data["template_id"],
        version=str(data["version"]),
        valid_from=str(data.get("valid_from", "")),
        width=width,
        height=height,
        dpi=int(ref.get("dpi", 300)),
        fields=tuple(fields),
        alignment=alignment,
        code_sets=data.get("code_sets", {}),
        raw=data,
    )
