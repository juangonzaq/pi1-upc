"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, cropUrl, errorText } from "@/lib/api";
import { TYPE_LABEL, VALUE_SOURCE, displayLabel, fieldTitle, num } from "@/lib/format";
import type { Catalogs, Field } from "@/lib/types";
import { Button, ConfidenceBar, Icon, Kbd, RouteBadge, StatusPill, TruthMark, cx, inputClass } from "@/components/ui";

export interface Decision {
  action: "confirm" | "correct" | "choose" | "clear";
  value?: string;
  text?: string;
}

const FINAL_LABEL: Record<Decision["action"], string> = {
  confirm: "Confirmado",
  correct: "Tu corrección",
  choose: "Candidata elegida",
  clear: "Marcado como vacío",
};

/** El valor con el que queda el campo después de la decisión. */
function decidedValue(d: Decision, f: Field): string | null {
  if (d.action === "clear") return null;
  if (d.action === "confirm") return d.value ?? f.value;
  return d.value ?? d.text ?? null;
}

/** Lo que quedó en el registro validado, en la misma forma que una decisión de la revisión en curso. */
export function recordedDecision(f: Field): Decision | undefined {
  const v = f.validated;
  if (!v || v.source === "auto") return undefined;
  if (v.value === null) return { action: "clear" };
  if (v.source === "confirmed") return { action: "confirm", value: v.value };
  return { action: v.source === "candidate" ? "choose" : "correct", value: v.value };
}

function sameAsTruth(value: string | null, truth: string | null, type: Field["type"]): boolean {
  const norm = (v: string | null) => (v ?? "").replace(/\s+/g, "").toUpperCase();
  if (type === "numeric" && value !== null && truth !== null) return Number(value) === Number(truth);
  return norm(value) === norm(truth);
}

const PLACEHOLDER: Record<string, string> = {
  numeric: "Por ejemplo 12.5",
  time: "HH:MM, por ejemplo 07:30",
  date: "AAAA-MM-DD",
  code: "Código",
  free_text: "Texto",
};

/** El campo que se está revisando: recorte grande, lectura, motivos y las acciones del validador. */
export function FieldFocus({ docId, field: f, threshold, decision, undoable, reviewable, catalogs, editing, setEditing, onDecide, hasRecord, blocked }: {
  docId: string;
  field: Field;
  threshold: number | null | undefined;
  decision: Decision | undefined;
  undoable: boolean;
  reviewable: boolean;
  catalogs: Catalogs | null;
  editing: boolean;
  setEditing: (v: boolean) => void;
  onDecide: (d: Decision | undefined) => void;
  hasRecord: boolean;
  blocked: boolean;
}) {
  const [trace, setTrace] = useState<string | null>(null);
  const canClear = f.required !== "always";
  const canConfirm = !(f.value === null && f.route === "REVIEW");
  const final = decision ? decidedValue(decision, f) : null;

  return (
    <article className={cx("border bg-surface", blocked ? "border-error" : "border-line")} aria-label={`Campo ${fieldTitle(f)}`}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-3">
        <div className="min-w-0">
          <div className="text-[0.8rem] text-ink-3">{f.table ? `${f.block_label}, fila ${f.row}` : f.block_label}</div>
          <h2 className="font-display text-xl font-medium leading-snug">{displayLabel(f.label)}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="h-6 bg-paper px-2 text-[0.8rem] leading-6 text-ink-2">{TYPE_LABEL[f.type]}</span>
          {decision ? <StatusPill status="decided">Decidido</StatusPill> : <RouteBadge route={f.route} />}
        </div>
      </header>

      <div className="flex min-h-20 items-center justify-center border-b border-line bg-paper-2 px-5 py-3">
        {f.crop ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={cropUrl(f.crop)} alt={`Recorte de ${displayLabel(f.label)}`} className="crop-img h-16 w-auto max-w-full border border-line object-contain" />
        ) : (
          <span className="text-sm text-ink-3">{f.mark === "repeat" ? "Trazo de repetición: el valor se hereda de la fila anterior" : "Sin recorte"}</span>
        )}
      </div>

      {decision ? (
        <div className="grid grid-cols-2 border-b border-line">
          <div className="min-w-0 px-5 py-3.5">
            <div className="text-[0.8rem] text-ink-3">Leyó el modelo</div>
            <div className={cx("mt-0.5 truncate font-mono text-lg text-ink-3", decision.action !== "confirm" && "line-through")}>
              {f.text ?? <span className="font-sans text-sm">sin lectura</span>}
            </div>
          </div>
          <div className="min-w-0 border-l-[3px] border-decided bg-decided-tint px-4 py-3.5">
            <div className="flex items-center gap-1.5 text-[0.8rem] text-ink-2"><Icon name="decided" className="size-3.5 text-decided" />{FINAL_LABEL[decision.action]}</div>
            <div className="mt-0.5 truncate font-mono text-lg font-medium text-ink">{final ?? <span className="font-sans text-sm text-ink-2">vacío</span>}</div>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="text-[0.8rem] text-ink-3">Leído</div>
            <div className="mt-0.5 truncate font-mono text-lg">{f.text ?? <span className="font-sans text-sm text-ink-3">sin lectura</span>}</div>
          </div>
          <div className="min-w-0">
            <div className="text-[0.8rem] text-ink-3">Valor propuesto</div>
            <div className="mt-0.5 truncate font-mono text-lg">{f.value ?? <span className="font-sans text-sm text-ink-3">vacío</span>}</div>
            {f.value_label && <div className="truncate text-[0.8rem] text-ink-2">{f.value_label}</div>}
          </div>
        </div>
      )}
      {reviewable && (
        <div className="px-5 py-4">
          {editing ? (
            <Editor field={f} catalogs={catalogs} initial={decision} onCancel={() => setEditing(false)} onSave={(d) => { setEditing(false); onDecide(d); }} />
          ) : decision ? (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Cambiar <Kbd>E</Kbd>
              </Button>
              {undoable && <Button variant="ghost" onClick={() => onDecide(undefined)}>Deshacer</Button>}
            </div>
          ) : (
            <>
              {f.homologation && f.homologation.candidates.length > 0 && (
                <div className="mb-4">
                  <div className="mb-2 text-[0.8rem] text-ink-3">Candidatas del maestro</div>
                  <div className="flex flex-col gap-1.5">
                    {f.homologation.candidates.map((c, i) => (
                      <button
                        key={c.code}
                        onClick={() => onDecide({ action: "choose", value: c.code })}
                        className="flex items-center gap-3 border border-line px-3 py-2 text-left text-sm hover:border-ink-3"
                      >
                        <Kbd>{i + 1}</Kbd>
                        <span className="font-mono">{c.code}</span>
                        <span className="min-w-0 flex-1 truncate text-ink-2">{c.description}</span>
                        <span className="tabular text-[0.8rem] text-ink-3">{c.score.toFixed(2)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => onDecide({ action: "confirm" })} disabled={!canConfirm} title={canConfirm ? undefined : "No hay un valor propuesto que confirmar"}>
                  Confirmar <Kbd>↵</Kbd>
                </Button>
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Corregir <Kbd>E</Kbd>
                </Button>
                {canClear && (
                  <Button variant="secondary" onClick={() => onDecide({ action: "clear" })}>
                    Vacío <Kbd>V</Kbd>
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-4 gap-y-3 border-t border-line px-5 py-4 text-sm">
        <dt className="text-ink-3">Confianza del modelo</dt>
        <dd>
          <ConfidenceBar value={f.confidence} threshold={threshold} reliable={f.reliable} wide />
          <div className="mt-1 text-[0.78rem] text-ink-3">{threshold == null ? "Este tipo de campo no tiene umbral calibrado." : `Umbral del tipo ${num(threshold)}; sin calibrar ${num(f.raw_score)}.`}</div>
        </dd>
        {!decision && (f.reasons.length > 0 || f.violations.length > 0) && (
          <>
            <dt className="text-ink-3">Por qué se revisa</dt>
            <dd>
              <ul className="space-y-1">
                {f.reasons.map((r) => (
                  <li key={r.code} className="flex gap-2"><Icon name="review" className="mt-0.5 size-3.5 text-review" />{r.text}</li>
                ))}
                {f.violations.map((v, i) => (
                  <li key={`${v.rule}-${i}`} className="flex gap-2"><Icon name="error" className="mt-0.5 size-3.5 text-error" />{v.message}</li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {f.truth && (
          <>
            <dt className="text-ink-3">Verdad (demo)</dt>
            <dd className="flex flex-wrap items-baseline gap-x-3">
              <span className="font-mono">{f.truth.text ?? (f.truth.mark === "repeat" ? "〃" : "vacío")}</span>
              <span className="text-ink-3">valor</span>
              <span className="font-mono">{f.truth.value ?? "vacío"}</span>
              {decision ? (
                <TruthMark correct={sameAsTruth(final, f.truth.value, f.type)} yes="coincide con tu decisión" no="no coincide con tu decisión" />
              ) : (
                <TruthMark correct={f.correct} />
              )}
            </dd>
          </>
        )}
        {f.validated && !decision && (
          <>
            <dt className="text-ink-3">Validado</dt>
            <dd><span className="font-mono">{f.validated.value ?? "vacío"}</span> <span className="text-ink-3">({VALUE_SOURCE[f.validated.source] ?? f.validated.source})</span></dd>
          </>
        )}
        {f.homologation && (
          <>
            <dt className="text-ink-3">Maestro</dt>
            <dd className="text-ink-2">
              «{f.homologation.normalized}» se homologó a {f.homologation.code ?? "ningún código"} ({f.homologation.method}, {num(f.homologation.score, 2)}){f.homologation.accepted ? "" : ", bajo el umbral"}
            </dd>
          </>
        )}
      </dl>

      {hasRecord && (
        <details className="border-t border-line px-5 py-3 text-sm" onToggle={async (e) => {
          if ((e.target as HTMLDetailsElement).open && !trace) {
            try {
              setTrace(JSON.stringify(await api(`/api/documents/${docId}/trace/${f.id}`), null, 2));
            } catch (err) {
              setTrace(errorText(err));
            }
          }
        }}>
          <summary className="cursor-pointer text-ink-2 hover:text-ink">Trazabilidad del valor validado</summary>
          <pre className="mt-2 max-h-64 overflow-auto bg-ink p-3 text-[0.72rem] leading-relaxed text-white">{trace ?? "Cargando…"}</pre>
        </details>
      )}
    </article>
  );
}

function Editor({ field: f, catalogs, initial, onCancel, onSave }: {
  field: Field;
  catalogs: Catalogs | null;
  initial: Decision | undefined;
  onCancel: () => void;
  onSave: (d: Decision) => void;
}) {
  const [value, setValue] = useState(initial?.action === "correct" || initial?.action === "choose" ? initial.value ?? "" : f.value ?? "");
  const [text, setText] = useState(initial?.action === "correct" ? initial.text ?? "" : "");
  const first = useRef<HTMLInputElement & HTMLSelectElement>(null);
  const options = f.master_type ? catalogs?.masters[f.master_type] : f.code_set ? catalogs?.code_sets[f.code_set] : undefined;

  useEffect(() => {
    first.current?.focus();
    first.current?.select?.();
  }, []);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!value.trim() && !text.trim()) return;
    onSave({ action: "correct", value: value.trim() || undefined, text: text.trim() || undefined });
  }

  return (
    <form onSubmit={submit} onKeyDown={(e) => e.key === "Escape" && onCancel()} className="space-y-3">
      <div>
        <label className="mb-1.5 block text-sm text-ink-2" htmlFor="fix-value">Valor correcto{f.master_type ? " (código del maestro)" : ""}</label>
        {options && options.length > 0 ? (
          <select id="fix-value" ref={first} className={inputClass} value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="">Elegir</option>
            {options.map((o) => (
              <option key={o.code} value={o.code}>{o.code}{o.description ? `, ${o.description}` : ""}</option>
            ))}
          </select>
        ) : (
          <input id="fix-value" ref={first} className={cx(inputClass, "font-mono")} placeholder={PLACEHOLDER[f.type]} value={value} onChange={(e) => setValue(e.target.value)} />
        )}
      </div>
      <div>
        <label className="mb-1.5 block text-sm text-ink-2" htmlFor="fix-text">Texto tal como está escrito (opcional)</label>
        <input id="fix-text" className={cx(inputClass, "font-mono")} placeholder={f.text ? `El modelo leyó «${f.text}»` : "Lo que dice el formato"} value={text} onChange={(e) => setText(e.target.value)} />
        <p className="mt-1.5 text-[0.78rem] text-ink-3">Mejora la etiqueta con la que se reentrena el modelo.</p>
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={!value.trim() && !text.trim()}>Guardar corrección <Kbd>↵</Kbd></Button>
        <Button type="button" variant="ghost" onClick={onCancel}>Cancelar <Kbd>Esc</Kbd></Button>
      </div>
    </form>
  );
}

/** Lista de campos del documento; el actual queda marcado y se sigue con el teclado. */
export function FieldQueue({ fields, current, decisions, onSelect }: {
  fields: Field[];
  current: string | null;
  decisions: Record<string, Decision>;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<HTMLUListElement>(null);
  useEffect(() => {
    // desplaza solo la lista (scrollIntoView movería también la página)
    const list = ref.current;
    const el = list?.querySelector<HTMLElement>(`[data-field="${CSS.escape(current ?? "")}"]`);
    if (!list || !el) return;
    if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop;
    else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight;
  }, [current]);

  return (
    <ul ref={ref} className="relative max-h-[26rem] divide-y divide-line overflow-auto border border-line bg-surface">
      {fields.map((f) => {
        const d = decisions[f.id];
        const on = f.id === current;
        const glyph = d ? { name: "decided" as const, c: "text-decided", t: "Decidido" } : f.route === "AUTO" ? { name: "check" as const, c: "text-auto", t: "Automático" } : { name: "review" as const, c: "text-review", t: "A revisión" };
        return (
          <li key={f.id} data-field={f.id}>
            <button
              onClick={() => onSelect(f.id)}
              aria-current={on ? "true" : undefined}
              className={cx("grid w-full grid-cols-[1rem_minmax(0,1fr)_minmax(0,0.8fr)] items-center gap-3 border-l-[3px] px-4 py-2.5 text-left text-sm", on ? "border-gold bg-gold-tint" : "border-transparent hover:bg-paper-2")}
            >
              <Icon name={glyph.name} className={cx("size-3.5", glyph.c)} />
              <span className="min-w-0">
                <span className="block truncate">{displayLabel(f.label)}</span>
                <span className="block truncate text-[0.75rem] text-ink-3">{f.table ? `${f.block_label}, fila ${f.row}` : f.block_label}</span>
              </span>
              <span className="truncate text-right font-mono text-[0.82rem] text-ink-2">
                {d ? (d.action === "clear" ? "vacío" : d.value ?? f.value ?? "") : f.text ?? ""}
                {f.correct === false && !d && <Icon name="error" className="ml-1.5 inline size-3 text-error" />}
              </span>
              <span className="sr-only">{glyph.t}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
