"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, errorText, post, useApi } from "@/lib/api";
import { BATCH_LABEL, STAGE_LABEL, STATE_LABEL, dateTime, pct, secs } from "@/lib/format";
import { loadDraft, loadProcessing, saveDraft, saveProcessing, usePref } from "@/lib/prefs";
import type { Catalogs, DocDetail, Field, ProcessResult } from "@/lib/types";
import { ModelSelect } from "@/components/model-select";
import { NextReviewButton } from "@/components/next-review";
import { SetupHelp } from "@/components/setup-help";
import { Button, Empty, ErrorBox, Icon, Kbd, Label, Loading, Meter, Notice, Segmented, StateBadge, cx, inputClass } from "@/components/ui";
import { FieldFocus, FieldQueue, recordedDecision, type Decision } from "./field-review";
import { PageViewer } from "./page-viewer";

type Tab = "pending" | "review" | "auto" | "wrong" | "all";
type LastProcessing = Partial<ProcessResult> & { outcome?: string };
type Flash = { text: string; nextReview?: boolean };

export function DocumentView() {
  const { id } = useParams<{ id: string }>();
  const { data, error, reload } = useApi<DocDetail>(`/api/documents/${id}`);
  const catalogs = useApi<Catalogs>("/api/catalogs");
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [current, setCurrent] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab | null>(null);
  const [editing, setEditing] = useState(false);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [last, setLast] = useState<LastProcessing | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);

  useEffect(() => {
    const saved = loadProcessing<LastProcessing>(id);
    // sessionStorage solo existe en el navegador: se lee tras montar
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLast(saved);
  }, [id]);

  const fields = useMemo(() => data?.fields ?? [], [data]);
  const reviewable = data?.document.state === "EN_REVISION" && fields.length > 0;
  const runId = data?.run?.run_id ?? "";

  useEffect(() => {
    // localStorage solo existe en el navegador: se lee tras montar
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (runId && reviewable) setDecisions(loadDraft<Decision>(id, runId));
  }, [id, runId, reviewable]);

  // cada cambio se guarda al momento: recargar o salir de la página no borra lo decidido
  const keep = useCallback(
    (next: Record<string, Decision>) => {
      setDecisions(next);
      saveDraft(id, runId, next);
    },
    [id, runId],
  );
  const activeTab: Tab = tab ?? (reviewable ? "pending" : "all");
  const isPending = useCallback((f: Field, ds: Record<string, Decision>) => f.route === "REVIEW" && !ds[f.id] && !f.validated, []);
  // lo ya validado se ve igual que una decisión; al validar solo se envía lo decidido en esta revisión
  const shown = useMemo(() => {
    const all: Record<string, Decision> = {};
    for (const f of fields) {
      const d = decisions[f.id] ?? recordedDecision(f);
      if (d) all[f.id] = d;
    }
    return all;
  }, [fields, decisions]);
  const queue = useMemo(
    () =>
      fields.filter((f) =>
        activeTab === "pending" ? isPending(f, decisions) : activeTab === "review" ? f.route === "REVIEW" : activeTab === "auto" ? f.route === "AUTO" : activeTab === "wrong" ? f.correct === false : true,
      ),
    [fields, activeTab, decisions, isPending],
  );
  // el campo actual se deriva: el elegido, o el primero de la cola
  const cur = current && fields.some((f) => f.id === current) ? current : queue[0]?.id ?? fields[0]?.id ?? null;
  const curField = fields.find((f) => f.id === cur) ?? null;

  const select = useCallback((fid: string) => {
    setCurrent(fid);
    setEditing(false);
  }, []);

  // el siguiente campo sin decidir, en el orden del formato
  const nextPending = useCallback(
    (fid: string, ds: Record<string, Decision>) => {
      const order = fields.filter((f) => f.route === "REVIEW");
      const at = order.findIndex((f) => f.id === fid);
      return [...order.slice(at + 1), ...order.slice(0, Math.max(0, at))].find((f) => isPending(f, ds));
    },
    [fields, isPending],
  );

  const decide = useCallback(
    (fid: string, d: Decision | undefined) => {
      const next = { ...decisions };
      if (d) next[fid] = d;
      else delete next[fid];
      keep(next);
      setBlocked((b) => b.filter((x) => x !== fid));
      const after = d && nextPending(fid, next);
      if (after) setCurrent(after.id);
    },
    [decisions, keep, nextPending],
  );

  const move = useCallback(
    (delta: number) => {
      const list = queue.length ? queue : fields;
      const at = list.findIndex((f) => f.id === cur);
      const target = list[Math.min(list.length - 1, Math.max(0, (at < 0 ? 0 : at) + delta))];
      if (target) select(target.id);
    },
    [queue, fields, cur, select],
  );

  // atajos de teclado del validador (fuera de los campos de texto)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || editing) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        move(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        move(-1);
      } else if (!reviewable || !curField) {
        return;
      } else if (e.key === "Enter" && t.tagName !== "BUTTON") {
        if (shown[curField.id]) {
          // un campo ya decidido no se vuelve a decidir con Enter: se pasa al siguiente
          e.preventDefault();
          const after = nextPending(curField.id, decisions);
          if (after) select(after.id);
        } else if (!(curField.value === null && curField.route === "REVIEW")) {
          e.preventDefault();
          decide(curField.id, { action: "confirm" });
        }
      } else if (e.key === "e") {
        e.preventDefault();
        setEditing(true);
      } else if (e.key === "v" && curField.required !== "always") {
        decide(curField.id, { action: "clear" });
      } else if (["1", "2", "3"].includes(e.key) && curField.homologation?.candidates[Number(e.key) - 1]) {
        decide(curField.id, { action: "choose", value: curField.homologation.candidates[Number(e.key) - 1].code });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, decide, curField, reviewable, editing, decisions, shown, nextPending, select]);

  if (error && !data) return error.includes("conectar") ? <SetupHelp error={error} /> : <ErrorBox onRetry={reload}>{error}</ErrorBox>;
  if (!data) return <Loading />;

  const doc = data.document;
  const thresholds = data.run?.thresholds ?? {};
  const reviewTotal = fields.filter((f) => f.route === "REVIEW").length;
  const pendingCount = fields.filter((f) => isPending(f, decisions)).length;
  const decidedCount = reviewTotal - pendingCount;
  const s = data.summary;

  function fillFromTruth() {
    const next: Record<string, Decision> = { ...decisions };
    for (const f of fields) {
      if (f.route !== "REVIEW" || !f.truth || next[f.id]) continue;
      // la verdad no trae este campo obligatorio: lo decide quien revisa
      if (f.truth.value === null && f.required === "always") continue;
      if (f.truth.value === null) next[f.id] = { action: "clear" };
      else if (f.correct) next[f.id] = { action: "confirm" };
      else next[f.id] = { action: "correct", value: f.truth.value, text: f.truth.text ?? undefined };
    }
    keep(next);
  }

  const title = doc.form_id ?? doc.source_name;
  const tabs: { value: Tab; label: string; count: number }[] = [
    ...(reviewable ? [{ value: "pending" as Tab, label: "Por decidir", count: pendingCount }] : []),
    { value: "review", label: "A revisión", count: reviewTotal },
    { value: "auto", label: "Automáticos", count: fields.filter((f) => f.route === "AUTO").length },
    ...(s.truth_available ? [{ value: "wrong" as Tab, label: "Errores vs verdad", count: fields.filter((f) => f.correct === false).length }] : []),
    { value: "all", label: "Todos", count: fields.length },
  ];

  return (
    <div className={cx(reviewable && "pb-28")}>
      <nav aria-label="Ruta" className="mb-3 text-sm text-ink-3">
        <Link href="/documentos" className="hover:text-ink hover:underline">Revisión</Link>
        <span aria-hidden className="mx-2">/</span>
        <span className="text-ink-2">{title}</span>
      </nav>

      <header className="mb-6 flex flex-wrap items-end justify-between gap-6 border-b border-line pb-6">
        <div className="min-w-0">
          <h1 className="font-display text-[2rem] font-medium leading-tight">Formato {title}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-ink-2">
            <StateBadge state={doc.state} />
            {doc.batch && <span>{BATCH_LABEL[doc.batch] ?? doc.batch}</span>}
            <span className="tabular">{doc.business_key ? doc.business_key.split("|").join("   ") : "Sin fecha, guardia ni equipo"}</span>
            {data.run?.model_version && <span>Leído con {data.run.model_version}</span>}
          </div>
        </div>
        <Reprocess docId={doc.id} state={doc.state} onDone={(r) => { keep({}); setLast(r); setFlash(null); reload(); }} />
      </header>

      <div className="mb-6 space-y-3">
        {flash && (
          <Notice tone="emerald">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{flash.text}</span>
              {flash.nextReview && <NextReviewButton except={doc.id} variant="dark" />}
            </div>
          </Notice>
        )}
        {doc.quality_warnings?.map((w) => <Notice key={w} tone="amber">{w}</Notice>)}
        {last?.outcome === "repetida" && <Notice tone="amber">Este archivo ya estaba registrado: se muestra el formato existente, sin reprocesarlo.</Notice>}
        {last?.outcome === "reintentada" && <Notice tone="amber">Este archivo ya estaba registrado y había fallado: se procesó de nuevo.</Notice>}
        {doc.cause && (doc.state === "ERROR" || doc.state === "DUPLICADO_PENDIENTE" || fields.length === 0) && (
          <ErrorBox><strong className="font-medium">{STATE_LABEL[doc.state]}.</strong> {doc.cause}</ErrorBox>
        )}
      </div>

      {fields.length > 0 && (
        <dl className="mb-8 grid grid-cols-2 gap-px border border-line bg-line sm:grid-cols-3 xl:grid-cols-6">
          <Fact label="Campos con contenido" value={s.fields} />
          <Fact label="Aceptados solos" value={s.auto} detail={pct(s.fields ? s.auto / s.fields : null)} />
          <Fact label="A revisión" value={s.review} />
          {s.truth_available ? (
            <>
              <Fact label="Correctos frente a la verdad" value={pct(s.truth_compared ? s.truth_correct / s.truth_compared : null, 0)} detail={`${s.truth_correct} de ${s.truth_compared}`} />
              <Fact label="Errores aceptados solos" value={s.auto_errors} detail="riesgo del enrutamiento" />
            </>
          ) : (
            <Fact label="Verdad de referencia" value="—" detail="sin verdad para este archivo" />
          )}
          <Fact
            label="Procesamiento"
            value={last?.seconds != null ? secs(last.seconds) : "—"}
            detail={last?.stages ? Object.entries(last.stages).map(([k, v]) => `${STAGE_LABEL[k] ?? k} ${secs(v)}`).join(", ") : "tiempo de la última lectura en esta sesión"}
          />
        </dl>
      )}

      {doc.state === "VALIDADO" && (
        <div className="mb-8">
          <ApprovalPanel docId={doc.id} onDone={(msg) => { setFlash({ text: msg }); reload(); }} />
        </div>
      )}

      {fields.length === 0 ? (
        <Empty>
          {doc.state === "ERROR"
            ? "No hay campos: la imagen no pasó el control de calidad o no se reconoció la plantilla. Corrija la causa y vuelva a procesarla."
            : doc.has_preprocessed
              ? "No hay campos reconocidos: la alineación quedó fuera de tolerancia. Reprocese o vuelva a capturar el formato."
              : "El formato aún no se procesa."}
        </Empty>
      ) : (
        <div className="grid gap-8 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <div className="xl:sticky xl:top-28 xl:self-start">
            <PageViewer docId={doc.id} fields={fields} current={cur} decided={new Set(Object.keys(shown))} onSelect={select} />
          </div>

          <div className="min-w-0 space-y-5">
            {reviewable && (
              <div>
                <div className="flex items-baseline justify-between gap-3">
                  <div className="font-display text-lg">
                    {decidedCount} de {reviewTotal} decididos
                  </div>
                  {s.truth_available && (
                    <button onClick={fillFromTruth} className="text-sm text-ink-3 underline underline-offset-4 hover:text-ink" title="Solo para la demo: decide cada campo con la verdad sintética">
                      Rellenar con la verdad
                    </button>
                  )}
                </div>
                <Meter value={reviewTotal ? decidedCount / reviewTotal : 1} status="decided" className="mt-2" label="Campos decididos" />
              </div>
            )}

            {curField ? (
              <FieldFocus
                docId={doc.id}
                field={curField}
                threshold={thresholds[curField.type]}
                decision={shown[curField.id]}
                undoable={Boolean(decisions[curField.id])}
                reviewable={reviewable}
                catalogs={catalogs.data}
                editing={editing}
                setEditing={setEditing}
                onDecide={(d) => decide(curField.id, d)}
                hasRecord={data.records.length > 0}
                blocked={blocked.includes(curField.id)}
              />
            ) : (
              <Empty>No quedan campos por decidir en este formato. Valide el registro abajo.</Empty>
            )}

            <div className="flex items-center justify-between text-sm text-ink-3">
              <button className="inline-flex items-center gap-1.5 hover:text-ink" onClick={() => move(-1)}>
                <Icon name="chevronLeft" /> Anterior <Kbd>K</Kbd>
              </button>
              <button className="inline-flex items-center gap-1.5 hover:text-ink" onClick={() => move(1)}>
                <Kbd>J</Kbd> Siguiente <Icon name="chevronRight" />
              </button>
            </div>

            <div>
              <Segmented label="Campos del formato" value={activeTab} onChange={(v) => setTab(v)} options={tabs} />
              <div className="mt-3">
                {queue.length ? (
                  <FieldQueue fields={queue} current={cur} decisions={shown} onSelect={select} />
                ) : (
                  <p className="border border-line bg-surface px-4 py-6 text-center text-sm text-ink-3">No hay campos en esta vista.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <History data={data} />

      {reviewable && (
        <ValidateBar
          docId={doc.id}
          decisions={decisions}
          pending={pendingCount}
          onBlocked={(fids) => {
            setBlocked(fids);
            if (fids[0]) select(fids[0]);
          }}
          onDone={(msg) => {
            keep({});
            setBlocked([]);
            setFlash({ text: msg, nextReview: true });
            window.scrollTo({ top: 0, behavior: "smooth" });
            reload();
          }}
        />
      )}
    </div>
  );
}

function Fact({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="bg-surface px-4 py-3.5">
      <dt className="text-[0.8rem] text-ink-3">{label}</dt>
      <dd className="mt-1 font-display text-2xl font-medium leading-none">{value}</dd>
      {detail && <dd className="mt-1.5 truncate text-[0.75rem] text-ink-3" title={detail}>{detail}</dd>}
    </div>
  );
}

function Reprocess({ docId, state, onDone }: { docId: string; state: string; onDone: (r: LastProcessing) => void }) {
  const [model, setModel] = useState("");
  const [user] = usePref("perfo:user", "validador.web");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const duplicate = state === "DUPLICADO_PENDIENTE";
  if (!["CARGADO", "ERROR", "EN_REVISION", "DUPLICADO_PENDIENTE"].includes(state)) return null;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<ProcessResult>(`/api/documents/${docId}/${duplicate ? "confirm-duplicate" : "process"}`, { model: model || null, user });
      saveProcessing(docId, r);
      onDone(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-end gap-2">
        <div className="w-64">
          <Label htmlFor="reprocess-model">Modelo</Label>
          <ModelSelect id="reprocess-model" value={model} onChange={setModel} />
        </div>
        <Button variant={duplicate ? "primary" : "secondary"} busy={busy} onClick={run}>
          <Icon name="refresh" />
          {duplicate ? "Confirmar duplicado y procesar" : state === "EN_REVISION" ? "Volver a leer" : "Procesar"}
        </Button>
      </div>
      {error && <span className="max-w-md text-right text-sm text-error">{error}</span>}
    </div>
  );
}

function ValidateBar({ docId, decisions, pending, onBlocked, onDone }: {
  docId: string;
  decisions: Record<string, Decision>;
  pending: number;
  onBlocked: (fields: string[]) => void;
  onDone: (message: string) => void;
}) {
  const [user, setUser] = usePref("perfo:user", "validador.web");
  const [acceptRest, setAcceptRest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = pending === 0 || acceptRest;
  const unsaved = Object.keys(decisions).length;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ record: { version: number; sha256: string }; stats: { auto_accepted: number; warnings?: string[] }; touched: number; corrected: number; aliases: number }>(
        `/api/documents/${docId}/validate`,
        { user, decisions, accept_rest: acceptRest },
      );
      onDone(
        `Registro validado (versión ${r.record.version}): ${r.stats.auto_accepted} automáticos, ${r.touched} revisados, ${r.corrected} corregidos y ${r.aliases} alias aprendidos. Las correcciones quedan listas para el próximo reentrenamiento.` +
          (r.stats.warnings?.length ? ` Advertencias: ${r.stats.warnings.join("; ")}.` : ""),
      );
    } catch (e) {
      setError(errorText(e));
      if (e instanceof ApiError && e.fields.length) onBlocked(e.fields);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-8">
        {error ? (
          <div className="flex min-w-0 flex-1 items-center gap-2 text-sm text-error"><Icon name="error" />{error}</div>
        ) : (
          <div className="text-sm text-ink-2">
            {pending === 0 ? "Todos los campos a revisión tienen decisión." : pending === 1 ? "Falta 1 campo por decidir." : `Faltan ${pending} campos por decidir.`}
            <span className="ml-3 text-ink-3">{unsaved === 1 ? "1 decisión sin validar" : `${unsaved} decisiones sin validar`}</span>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" className="accent-[#24211f]" checked={acceptRest} onChange={(e) => setAcceptRest(e.target.checked)} />
          Confirmar el resto como propuesto
        </label>
        <div className="ml-auto flex items-center gap-2">
          <input aria-label="Validador" className={cx(inputClass, "w-48")} value={user} onChange={(e) => setUser(e.target.value)} placeholder="Quién valida" />
          <Button onClick={submit} busy={busy} disabled={!user.trim() || !ready} title={ready ? undefined : "Decida todos los campos o marque «Confirmar el resto»"}>
            Validar registro
          </Button>
        </div>
      </div>
    </div>
  );
}

function ApprovalPanel({ docId, onDone }: { docId: string; onDone: (message: string) => void }) {
  const [approver, setApprover] = usePref("perfo:approver", "Jefe de Guardia");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "APROBADO" | "OBSERVADO") {
    setBusy(decision);
    setError(null);
    try {
      await post(`/api/documents/${docId}/approve`, { decision, approver, comment });
      onDone(decision === "APROBADO" ? `Registro aprobado por ${approver}.` : `Registro observado por ${approver}. Vuelve a revisión con su comentario.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-line border-l-[3px] border-l-gold bg-surface px-5 py-5">
      <h2 className="font-display text-xl font-medium">Aprobación del Jefe de Guardia</h2>
      <p className="mt-1 text-sm text-ink-2">Si observa el registro, vuelve a revisión con el comentario. Si lo aprueba, queda cerrado.</p>
      {error && <div className="mt-4"><ErrorBox>{error}</ErrorBox></div>}
      <div className="mt-4 grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)_auto] md:items-end">
        <div>
          <Label htmlFor="approver">Aprobador</Label>
          <input id="approver" className={inputClass} value={approver} onChange={(e) => setApprover(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="comment">Comentario (obligatorio para observar)</Label>
          <input id="comment" className={inputClass} value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
        <div className="flex gap-2">
          <Button busy={busy === "APROBADO"} disabled={!approver.trim() || !!busy} onClick={() => decide("APROBADO")}>Aprobar</Button>
          <Button variant="secondary" busy={busy === "OBSERVADO"} disabled={!approver.trim() || !comment.trim() || !!busy} onClick={() => decide("OBSERVADO")}>Observar</Button>
        </div>
      </div>
    </section>
  );
}

function History({ data }: { data: DocDetail }) {
  return (
    <div className="mt-12 grid gap-8 border-t border-line pt-8 lg:grid-cols-2">
      <section>
        <h2 className="font-display text-lg font-medium">Historial</h2>
        <ol className="mt-4 border-l border-line">
          {data.history.map((h, i) => (
            <li key={i} className="relative pb-4 pl-5 last:pb-0">
              <span className="absolute -left-[5px] top-1.5 size-[9px] border border-ink-3 bg-surface" aria-hidden />
              <div className="text-sm">
                <span className="font-medium">{STATE_LABEL[h.to as keyof typeof STATE_LABEL] ?? h.to}</span>
                <span className="ml-2 text-ink-3">{dateTime(h.at)}, {h.actor}</span>
              </div>
              {h.cause && <div className="mt-0.5 text-[0.82rem] text-ink-2">{h.cause}</div>}
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h2 className="font-display text-lg font-medium">Registros validados</h2>
        {data.records.length === 0 ? (
          <p className="mt-4 text-sm text-ink-3">Aún no se valida. Cada validación crea una versión inmutable con su huella SHA-256.</p>
        ) : (
          <ul className="mt-4 space-y-4 text-sm">
            {data.records.map((r) => (
              <li key={r.id}>
                <div><span className="font-medium">Versión {r.version}</span> <span className="text-ink-3">{dateTime(r.validated_at)}, {r.validated_by}</span></div>
                <div className="text-[0.82rem] text-ink-2">
                  {String(r.stats.auto_accepted ?? 0)} automáticos, {String(r.stats.reviewed ?? 0)} revisados, {String(r.stats.corrected ?? 0)} corregidos
                </div>
                <div className="truncate font-mono text-[0.75rem] text-ink-3">{r.sha256}</div>
              </li>
            ))}
            {data.approvals.map((a, i) => (
              <li key={`a${i}`} className="flex flex-wrap items-center gap-2">
                <StateBadge state={a.decision === "APROBADO" ? "APROBADO" : "EN_REVISION"} />
                <span>{a.approver}</span>
                <span className="text-ink-3">{dateTime(a.at)}</span>
                {a.comment && <span className="text-ink-2">«{a.comment}»</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
