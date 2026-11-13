"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, post, useApi, useInterval } from "@/lib/api";
import { MODEL_STATUS, TYPE_LABEL, dateTime, num, pct, secs } from "@/lib/format";
import { usePref } from "@/lib/prefs";
import type { FieldType, Gate, Job, ModelInfo, ModelsResponse, PromoteResult, Status as ApiStatus } from "@/lib/types";
import { SetupHelp } from "@/components/setup-help";
import { Button, ErrorBox, Icon, Label, Loading, Meter, Notice, PageHeader, Spinner, StatusPill, cx, inputClass, type Status } from "@/components/ui";

const CYCLE_STATUS: Record<string, string> = {
  pendiente_aprobacion: "El candidato está listo y espera la decisión de promoverlo.",
  sin_correcciones: "No había correcciones nuevas: no se reentrenó.",
  bloqueado_g1: "Se detuvo en G1: integridad o fuga de datos.",
  entrenamiento_fallido: "El reentrenamiento falló; el modelo activo no cambia.",
};

const MODEL_TONE: Record<string, Status> = { active: "auto", candidate: "review", rejected: "error", retired: "neutral", baseline: "neutral" };
type Flash = { ok: boolean; text: string };

export function Models() {
  const { data, error, reload } = useApi<ModelsResponse>("/api/models");
  const status = useApi<ApiStatus>("/api/status");
  const [flash, setFlash] = useState<Flash | null>(null);
  if (error && !data) return <SetupHelp error={error} />;
  if (!data) return <Loading />;

  function refresh() {
    reload();
    status.reload();
  }

  return (
    <div>
      <PageHeader
        title="Modelos"
        description="El modelo nunca cambia solo. Cada reentrenamiento produce un candidato a partir de las correcciones de la revisión manual; reemplaza al activo únicamente si pasa las compuertas y alguien lo aprueba."
      />
      {flash && <div className="mb-6"><Notice tone={flash.ok ? "emerald" : "amber"}>{flash.text}</Notice></div>}
      <Cycle learning={data.learning} active={data.active} models={data.models} onFinished={refresh} />

      {status.data && <ActiveModel status={status.data} />}

      <section className="mt-12">
        <h2 className="font-display text-xl font-medium">Versiones</h2>
        <p className="mt-1 text-sm text-ink-2">De la más reciente a la línea base. Cada una indica de qué versión parte.</p>
        <ol className="mt-5 border-l-2 border-line">
          {data.models.map((m) => (
            <ModelNode
              key={m.version}
              m={m}
              onChanged={(msg) => {
                setFlash(msg);
                window.scrollTo({ top: 0, behavior: "smooth" });
                refresh();
              }}
            />
          ))}
        </ol>
      </section>

      {data.history.length > 0 && (
        <section className="mt-12">
          <h2 className="font-display text-xl font-medium">Activaciones</h2>
          <table className="mt-4 w-full border border-line bg-surface text-sm">
            <thead>
              <tr className="border-b border-line text-left text-ink-3">
                <th className="px-5 py-2.5 font-normal">Fecha</th>
                <th className="px-5 py-2.5 font-normal">Versión activada</th>
                <th className="px-5 py-2.5 font-normal">Reemplaza a</th>
                <th className="px-5 py-2.5 font-normal">Aprobó</th>
                <th className="px-5 py-2.5 font-normal">Motivo</th>
              </tr>
            </thead>
            <tbody>
              {[...data.history].reverse().map((h, i) => (
                <tr key={`${h.version}-${i}`} className="border-b border-line last:border-0">
                  <td className="px-5 py-2.5 text-ink-2">{dateTime(h.at)}</td>
                  <td className="px-5 py-2.5 font-medium">{h.version}</td>
                  <td className="px-5 py-2.5 text-ink-2">{h.previous ?? "ninguna"}</td>
                  <td className="px-5 py-2.5">{h.approver}</td>
                  <td className="px-5 py-2.5 text-ink-2">{h.note || h.reason || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function ActiveModel({ status }: { status: ApiStatus }) {
  const active = status.active_model;
  const total = status.fields.auto + status.fields.review;
  return (
    <section className="mt-12">
      <h2 className="font-display text-xl font-medium">Modelo activo</h2>
      <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-2">
        Un campo se acepta solo si su tipo tiene umbral calibrado y la confianza lo supera. Sin umbral, siempre va a revisión.
      </p>
      <div className="mt-4 border border-line bg-surface">
        <div className="border-b border-line px-5 py-4 font-display text-lg">{active?.version ?? "Línea base preentrenada"}</div>
        <div className="px-5 py-4">
          <div className="flex items-baseline justify-between text-sm text-ink-2">
            <span>Campos aceptados automáticamente en operación</span>
            <span className="font-display text-lg text-ink">{pct(total ? status.fields.auto / total : null)}</span>
          </div>
          <Meter value={total ? status.fields.auto / total : 0} status="auto" className="mt-2" label="Campos aceptados automáticamente en operación" />
          <div className="mt-1.5 text-[0.8rem] text-ink-3">{status.fields.auto} de {total} campos con contenido</div>
        </div>
        {active && (
          <table className="w-full border-t border-line text-sm">
            <thead>
              <tr className="text-left text-ink-3">
                <th className="px-5 py-2.5 font-normal">Tipo de campo</th>
                <th className="px-5 py-2.5 text-right font-normal">Temperatura</th>
                <th className="px-5 py-2.5 font-normal">Umbral de aceptación</th>
              </tr>
            </thead>
            <tbody>
              {(Object.keys(TYPE_LABEL) as FieldType[]).filter((t) => t !== "free_text").map((t) => {
                const thr = active.thresholds?.[t];
                const temp = active.temperatures?.[t];
                return (
                  <tr key={t} className="border-t border-line">
                    <td className="px-5 py-2.5">{TYPE_LABEL[t]}</td>
                    <td className="tabular px-5 py-2.5 text-right text-ink-2">{temp == null ? "—" : num(temp, 2)}</td>
                    <td className="px-5 py-2.5">
                      {thr == null ? <StatusPill status="review">Siempre a revisión</StatusPill> : <span className="tabular">{num(thr, 3)}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {status.learning.last_cycle && <p className="mt-4 text-sm text-ink-3">Último reentrenamiento: {status.learning.last_cycle.id}.</p>}
    </section>
  );
}

function Cycle({ learning, active, models, onFinished }: {
  learning: ModelsResponse["learning"];
  active: string | null;
  models: ModelInfo[];
  onFinished: () => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  // retoma el trabajo en curso (o el último) al volver a la página
  useEffect(() => {
    let alive = true;
    api<{ jobs: Job[] }>("/api/jobs")
      .then(async (r) => {
        const latest = r.jobs[0];
        if (latest && alive) setJob(await api<Job>(`/api/jobs/${latest.id}`));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  useInterval(
    async () => {
      if (!job) return;
      try {
        const next = await api<Job>(`/api/jobs/${job.id}?since=${job.log.length}`);
        setJob((cur) => (cur && cur.id === next.id ? { ...next, log: [...cur.log, ...next.log] } : cur));
        if (next.status !== "running") onFinished();
      } catch (e) {
        setError(errorText(e));
      }
    },
    job?.status === "running" ? 1500 : null,
  );

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [job?.log.length]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      setJob(await post<Job>("/api/cycle"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStarting(false);
    }
  }

  const running = job?.status === "running";
  const r = job?.result;
  const candidateStatus = r?.candidate ? models.find((m) => m.version === r.candidate)?.status : undefined;
  const outcome =
    candidateStatus === "active" ? "El candidato se promovió y hoy es el modelo activo."
      : candidateStatus === "rejected" ? "El candidato se rechazó al intentar promoverlo: falló alguna compuerta."
        : candidateStatus === "retired" ? "El candidato se promovió y luego lo reemplazó una versión posterior."
          : r ? CYCLE_STATUS[r.status] ?? r.status : "";

  return (
    <section className="border border-line bg-surface">
      <div className="grid gap-6 px-6 py-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div>
          <h2 className="font-display text-xl font-medium">Reentrenar con lo revisado</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-2">
            Parte del modelo activo ({active ?? "línea base"}) y usa las correcciones con doble peso, los valores confirmados y una muestra del histórico para no olvidar lo aprendido.
            Los formatos de la prueba congelada nunca se usan.
          </p>
        </div>
        <Button onClick={start} busy={starting || running} disabled={running || !learning.split_ready} className="lg:min-w-56">
          {running ? "Reentrenando" : "Reentrenar ahora"}
        </Button>
      </div>
      <dl className="grid grid-cols-2 gap-px border-t border-line bg-line md:grid-cols-4">
        <Fact label="Correcciones" value={learning.corrections} detail="el validador cambió el valor" />
        <Fact label="Confirmados a mano" value={learning.confirmed_review} detail="la propuesta era correcta" />
        <Fact label="Aceptados solos" value={learning.confirmed_auto} detail="ejemplos de baja prioridad" />
        <Fact label="Formatos validados" value={learning.documents} detail={learning.excluded_protected ? `${learning.excluded_protected} excluidos por la partición` : "sin exclusiones"} />
      </dl>
      {!learning.split_ready && <div className="border-t border-line px-6 py-4"><Notice tone="amber">No hay partición congelada para evaluar. Corra primero <code className="font-mono">make e2e-smoke</code>.</Notice></div>}
      {error && <div className="border-t border-line px-6 py-4"><ErrorBox>{error}</ErrorBox></div>}

      {job && (
        <div className="border-t border-line px-6 py-5">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {running ? <Spinner className="text-gold" /> : <Icon name={job.status === "done" ? "check" : "error"} className={job.status === "done" ? "text-auto" : "text-error"} />}
            <span className="font-medium">{running ? "Reentrenamiento en curso" : job.status === "done" ? "Último reentrenamiento" : "El último reentrenamiento falló"}</span>
            <span className="text-ink-3">empezó {dateTime(job.started_at)}{job.finished_at ? `, terminó ${dateTime(job.finished_at)}` : ""}</span>
          </div>
          <pre ref={logRef} className="mt-3 max-h-72 overflow-auto bg-ink p-4 font-mono text-[0.75rem] leading-relaxed text-[#e9e6df]">
            {job.log.join("\n") || "Iniciando"}
          </pre>
          {job.error && <div className="mt-3"><ErrorBox>{job.error}</ErrorBox></div>}
          {r && (
            <div className="mt-4">
              <p className="text-sm font-medium">{outcome}</p>
              {r.candidate && (
                <p className="mt-1 text-sm text-ink-2">
                  Candidato {r.candidate}: reduce la CER {r.ie5_pct == null ? "—" : `${r.ie5_pct.toFixed(1)} %`} frente al activo (IE5)
                  {r.latency && `, latencia p95 ${secs(r.latency.p95_s)}`}.
                </p>
              )}
              {r.gates && <div className="mt-3"><Gates gates={r.gates} /></div>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Fact({ label, value, detail }: { label: string; value: number | string; detail?: string }) {
  return (
    <div className="bg-surface px-6 py-4">
      <dt className="text-[0.8rem] text-ink-3">{label}</dt>
      <dd className="mt-1 font-display text-2xl font-medium leading-none">{value}</dd>
      {detail && <dd className="mt-1.5 text-[0.75rem] text-ink-3">{detail}</dd>}
    </div>
  );
}

function Gates({ gates, required }: { gates: Gate[]; required?: string[] }) {
  return (
    <ul className="grid gap-px border border-line bg-line sm:grid-cols-2 xl:grid-cols-3">
      {gates.map((g) => (
        <li key={g.gate} className="flex gap-2.5 bg-surface px-3.5 py-3 text-sm">
          <Icon name={g.passed ? "check" : "error"} className={cx("mt-0.5", g.passed ? "text-auto" : "text-error")} />
          <div className="min-w-0">
            <div>
              <span className="font-medium">{g.gate} {g.name}</span>
              <span className="ml-2 text-ink-3">{g.passed ? "pasa" : "no pasa"}{required && !required.includes(g.gate) ? ", informativa" : ""}</span>
            </div>
            <div className="mt-0.5 text-[0.78rem] leading-snug text-ink-2">{g.detail}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function ModelNode({ m, onChanged }: { m: ModelInfo; onChanged: (msg: Flash) => void }) {
  const [open, setOpen] = useState(m.active || m.status === "candidate");
  return (
    <li className="relative pb-6 pl-8 last:pb-0">
      <span className={cx("absolute -left-[7px] top-2 size-3", m.active ? "bg-gold" : "border-2 border-silver bg-surface")} aria-hidden />
      <div className={cx("border bg-surface", m.active ? "border-gold" : "border-line")}>
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 text-left">
          <span className="font-display text-lg font-medium">{m.version}</span>
          <StatusPill status={MODEL_TONE[m.status] ?? "neutral"}>{MODEL_STATUS[m.status] ?? m.status}</StatusPill>
          {!m.calibration && <span className="text-sm text-ink-3">sin calibrar: todo va a revisión</span>}
          <span className="ml-auto flex items-center gap-6 text-sm">
            <span><span className="text-ink-3">CER </span><span className="tabular">{num(m.metrics.cer, 4)}</span></span>
            <span><span className="text-ink-3">Aceptados solos </span><span className="tabular">{pct(m.routing?.coverage)}</span></span>
            <Icon name="chevronRight" className={cx("text-ink-3 transition-transform", open && "rotate-90")} />
          </span>
        </button>
        {open && (
          <div className="border-t border-line px-5 py-5">
            <p className="text-sm text-ink-2">
              {m.parent ? `Parte de ${m.parent}` : `Modelo preentrenado ${m.base_checkpoint}`}, creado {dateTime(m.created_at)}
              {m.dataset_snapshot ? `, entrenado con ${m.dataset_snapshot}` : ""}
              {m.training ? `; mejor paso ${m.training.best_step} de ${m.training.steps} en ${secs(m.training.seconds)}` : ""}.
            </p>
            <dl className="mt-4 grid grid-cols-2 gap-px border border-line bg-line sm:grid-cols-3 xl:grid-cols-6">
              <Metric label="CER en la prueba congelada" value={num(m.metrics.cer, 4)} />
              <Metric label="Exactitud por campo" value={pct(m.metrics.field_accuracy)} />
              <Metric label="CER, operadores no vistos" value={num(m.metrics.cer_unseen_operators, 4)} />
              <Metric label="Aceptados solos" value={pct(m.routing?.coverage)} />
              <Metric label="Error en lo aceptado (meta 1 %)" value={pct(m.routing?.residual_risk, 2)} warn={!!m.routing && m.routing.residual_risk > 0.01} />
              <Metric label="ECE (meta 0,05)" value={num(m.routing?.ece, 3)} warn={!!m.routing && m.routing.ece > 0.05} />
            </dl>
            {m.gates ? (
              <div className="mt-5">
                <div className="mb-2 text-sm text-ink-2">
                  Compuertas frente a {m.gates_against}. Para promover se exigen {m.required_gates.join(", ")}.
                </div>
                <Gates gates={m.gates} required={m.required_gates} />
              </div>
            ) : (
              <p className="mt-5 text-sm text-ink-3">Sin evaluación de compuertas.</p>
            )}
            {m.calibration && (
              <p className="mt-4 text-[0.82rem] text-ink-3">
                Umbrales por tipo:{" "}
                {Object.entries(m.calibration.thresholds).map(([t, v], i, all) => (
                  <span key={t} className="tabular">{t} {v == null ? "sin umbral" : v.toFixed(3)}{i < all.length - 1 ? "; " : ""}</span>
                ))}
              </p>
            )}
            {m.status === "candidate" && <Promote version={m.version} onDone={onChanged} />}
            {m.status_log.length > 0 && m.status !== "candidate" && (
              <p className="mt-3 text-[0.82rem] text-ink-3">Último cambio: {m.status_log[m.status_log.length - 1].note}, {dateTime(m.status_log[m.status_log.length - 1].at)}.</p>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="bg-surface px-4 py-3">
      <dt className="text-[0.75rem] leading-snug text-ink-3">{label}</dt>
      <dd className="mt-1 flex items-center gap-1.5 font-display text-lg">
        {warn && <Icon name="review" className="size-3.5 text-review" />}
        {value}
      </dd>
    </div>
  );
}

function Promote({ version, onDone }: { version: string; onDone: (msg: Flash) => void }) {
  const [approver, setApprover] = usePref("perfo:ml-approver", "Ing. de ML");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function promote() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<PromoteResult>(`/api/models/${version}/promote`, { approver, note });
      onDone(
        r.promoted
          ? { ok: true, text: `${version} promovido por ${approver}. Desde ahora los formatos se leen con esta versión (antes ${r.previous}).` }
          : { ok: false, text: `No se promovió ${version}: fallaron ${r.failed.join(", ")}. El modelo activo sigue siendo ${r.active}.` },
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-5 border-t border-line pt-5">
      <h3 className="font-display text-base font-medium">Promover a modelo activo (G6)</h3>
      <p className="mt-1 text-sm text-ink-2">Si falla una compuerta exigida, se rechaza y el modelo activo no cambia.</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-[13rem_minmax(0,1fr)_auto] sm:items-end">
        <div>
          <Label htmlFor={`ap-${version}`}>Aprobador</Label>
          <input id={`ap-${version}`} className={inputClass} value={approver} onChange={(e) => setApprover(e.target.value)} />
        </div>
        <div>
          <Label htmlFor={`note-${version}`}>Motivo</Label>
          <input id={`note-${version}`} className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <Button onClick={promote} busy={busy} disabled={!approver.trim()}>Promover</Button>
      </div>
      {error && <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>}
    </div>
  );
}
