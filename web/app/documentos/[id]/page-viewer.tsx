"use client";

import { useEffect, useRef, useState } from "react";
import { docImageUrl } from "@/lib/api";
import { fieldTitle } from "@/lib/format";
import type { Field } from "@/lib/types";
import { Icon, cx } from "@/components/ui";

const ZOOMS = [1, 2, 3] as const;

/** Imagen alineada con la plantilla y una caja por campo. Con zoom, sigue al campo que se está revisando. */
export function PageViewer({ docId, fields, current, decided, onSelect }: {
  docId: string;
  fields: Field[];
  current: string | null;
  decided: Set<string>;
  onSelect: (id: string) => void;
}) {
  const [zoom, setZoom] = useState<(typeof ZOOMS)[number]>(1);
  const [follow, setFollow] = useState(true);
  const [boxes, setBoxes] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scroller.current;
    const f = fields.find((x) => x.id === current);
    if (!el || !f || !follow || zoom === 1) return;
    const [x0, y0, x1, y1] = f.box;
    const inner = el.firstElementChild as HTMLElement | null;
    if (!inner) return;
    const w = inner.clientWidth;
    const h = inner.clientHeight;
    el.scrollTo({ left: ((x0 + x1) / 2) * w - el.clientWidth / 2, top: ((y0 + y1) / 2) * h - el.clientHeight / 2, behavior: "smooth" });
  }, [current, zoom, follow, fields]);

  return (
    <div className="border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-4 text-[0.8rem] text-ink-2">
          <span className="inline-flex items-center gap-1.5"><span className="size-3 border-2 border-auto" aria-hidden />Automático</span>
          <span className="inline-flex items-center gap-1.5"><span className="size-3 border-2 border-dashed border-review" aria-hidden />A revisión</span>
          <span className="inline-flex items-center gap-1.5"><span className="size-3 border-2 border-decided bg-decided/20" aria-hidden />Decidido</span>
        </div>
        <div className="flex items-center gap-3 text-[0.8rem]">
          <label className="flex items-center gap-1.5 text-ink-2">
            <input type="checkbox" className="accent-[#24211f]" checked={boxes} onChange={(e) => setBoxes(e.target.checked)} /> Cajas
          </label>
          <label className="flex items-center gap-1.5 text-ink-2" title="Con zoom, centra el campo que se está revisando">
            <input type="checkbox" className="accent-[#24211f]" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Seguir el campo
          </label>
          <div className="inline-flex border border-line" role="group" aria-label="Zoom">
            {ZOOMS.map((z) => (
              <button key={z} onClick={() => setZoom(z)} aria-pressed={zoom === z} className={cx("h-7 px-2.5", zoom === z ? "bg-ink text-white" : "text-ink-2 hover:bg-paper")}>
                {z === 1 ? "Ajustar" : `${z}×`}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div ref={scroller} className="max-h-[calc(100vh-14rem)] overflow-auto bg-paper">
        <div className="relative" style={{ width: `${zoom * 100}%` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={docImageUrl(docId, zoom > 1 ? 3000 : 1800)} alt="Formato alineado con la plantilla" className="block w-full select-none" draggable={false} />
          {boxes &&
            fields.map((f) => {
              const [x0, y0, x1, y1] = f.box;
              const isCurrent = current === f.id;
              const style = decided.has(f.id) ? "border-2 border-decided bg-decided/15" : f.route === "AUTO" ? "border border-auto bg-auto/5" : "border-2 border-dashed border-review bg-review/10";
              return (
                <button
                  key={f.id}
                  title={`${fieldTitle(f)}: ${f.text ?? "sin lectura"}`}
                  aria-label={fieldTitle(f)}
                  onClick={() => onSelect(f.id)}
                  className={cx("absolute transition-shadow hover:shadow-[0_0_0_2px_#24211f]", style, isCurrent && "z-10 shadow-[0_0_0_3px_#24211f,0_0_0_7px_rgba(194,154,57,0.55)]")}
                  style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                />
              );
            })}
        </div>
      </div>
      <div className="flex items-center gap-2 border-t border-line px-4 py-2 text-[0.78rem] text-ink-3">
        <Icon name="search" className="size-3.5" /> Haga clic en una caja para revisar ese campo.
      </div>
    </div>
  );
}
