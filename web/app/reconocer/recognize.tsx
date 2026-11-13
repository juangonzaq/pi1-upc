"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ChangeEvent, type DragEvent } from "react";
import { errorText, post, postForm, sampleImageUrl, useApi, useInterval } from "@/lib/api";
import { BATCH_LABEL } from "@/lib/format";
import { saveProcessing, usePref } from "@/lib/prefs";
import type { Health, IngestResult, Sample, UploadLimits } from "@/lib/types";
import { ModelSelect } from "@/components/model-select";
import { SetupHelp } from "@/components/setup-help";
import { Badge, Button, ErrorBox, Icon, Label, Notice, Spinner, StateBadge, cx, inputClass } from "@/components/ui";

const STEPS = ["Control de calidad", "Alineación con la plantilla", "Recorte de campos", "Lectura con TrOCR", "Reglas y enrutamiento"];
const ACCEPTED = ["image/jpeg", "image/png", "application/pdf"];
const MAX_BYTES = 10 * 1024 * 1024;

type Meta = { fecha: string; guardia: string; equipo: string };

export function Recognize() {
  const router = useRouter();
  const limits = useApi<Health>("/api/health").data?.upload;
  const [user, setUser] = usePref("perfo:user", "validador.web");
  const [model, setModel] = useState("");
  const [meta, setMeta] = useState<Meta>({ fecha: "", guardia: "", equipo: "" });
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  function choose(f: File | null) {
    if (!f) return;
    setError(null);
    if (!ACCEPTED.includes(f.type)) return setError(`${f.name} no es un archivo JPG, PNG o PDF.`);
    if (f.size > (limits?.max_bytes ?? MAX_BYTES)) return setError(`${f.name} pesa ${(f.size / 1e6).toFixed(1)} MB y el máximo es 10 MB.`);
    setFile(f);
    setSize(null);
    setPreview(f.type.startsWith("image/") ? URL.createObjectURL(f) : null);
  }

  function clear() {
    setFile(null);
    setPreview(null);
    setSize(null);
    setError(null);
  }

  // El archivo elegido sigue en pantalla si la lectura falla: se corrige o se reintenta sin volver a buscarlo.
  async function run(label: string, fn: () => Promise<IngestResult>) {
    setBusy(label);
    setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const res = await fn();
      saveProcessing(res.document_id, { outcome: res.outcome, ...(res.processing ?? {}) });
      router.push(`/documentos/${res.document_id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  function submit() {
    if (!file) return;
    const form = new FormData();
    form.set("file", file);
    form.set("user", user || "web");
    if (model) form.set("model", model);
    if (meta.fecha && meta.guardia && meta.equipo) {
      form.set("fecha", meta.fecha);
      form.set("guardia", meta.guardia);
      form.set("equipo", meta.equipo);
    }
    run(file.name, () => postForm<IngestResult>("/api/documents", form));
  }

  const resolution = size && limits ? resolutionOf(size, limits) : null;

  return (
    <div>
      <header className="mb-6">
        <h1 className="font-display text-[2rem] font-medium leading-tight tracking-[-0.01em] text-ink">Reconocer un formato</h1>
        <p className="mt-2 text-[0.95rem] leading-relaxed text-ink-2">Suba la foto o el escaneo del reporte. El sistema lee cada campo y le indica cuáles revisar.</p>
      </header>

      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Dropzone file={file} preview={preview} disabled={!!busy} onChoose={choose} onImage={setSize} />
        <div className="space-y-4">
          {error && <ErrorBox>{error}</ErrorBox>}
          {busy ? (
            <Processing label={busy} />
          ) : file ? (
            <Ready
              file={file}
              resolution={resolution}
              limits={limits}
              user={user}
              setUser={setUser}
              model={model}
              setModel={setModel}
              meta={meta}
              setMeta={setMeta}
              onClear={clear}
              onSubmit={submit}
            />
          ) : (
            <CaptureTips limits={limits} />
          )}
        </div>
      </div>

      <Samples disabled={!!busy} onProcess={(s) => run(s.sample_id, () => post<IngestResult>(`/api/samples/${s.sample_id}/process`, { user: user || "web", model: model || null }))} />
    </div>
  );
}

type Resolution = { dpi: number; level: "ok" | "low" | "rejected" };

// Mismo cálculo que el control de calidad del back (application/preprocess.py).
function resolutionOf(size: { w: number; h: number }, limits: UploadLimits): Resolution {
  const dpi = (Math.max(size.w, size.h) / limits.reference_long_px) * limits.template_dpi;
  return { dpi, level: dpi < limits.min_dpi ? "rejected" : dpi < limits.recommended_dpi ? "low" : "ok" };
}

const longSideFor = (dpi: number, limits: UploadLimits) => Math.ceil((dpi / limits.template_dpi) * limits.reference_long_px);

function Dropzone({ file, preview, disabled, onChoose, onImage }: {
  file: File | null;
  preview: string | null;
  disabled: boolean;
  onChoose: (f: File | null) => void;
  onImage: (size: { w: number; h: number }) => void;
}) {
  const [drag, setDrag] = useState(false);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    if (!disabled) onChoose(e.dataTransfer.files?.[0] ?? null);
  }

  function onChange(e: ChangeEvent<HTMLInputElement>) {
    onChoose(e.target.files?.[0] ?? null);
    e.target.value = ""; // permite volver a elegir el mismo archivo
  }

  return (
    <label
      aria-label={file ? "Cambiar el archivo" : "Elegir el archivo del formato"}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      className={cx(
        "flex min-h-[26rem] flex-col items-center justify-center border-2 bg-surface p-4 text-center transition-colors focus-within:ring-2 focus-within:ring-gold/40",
        disabled ? "cursor-progress" : "cursor-pointer",
        file ? "border-solid" : "border-dashed",
        drag ? "border-gold bg-gold-tint" : file ? "border-line" : "border-silver hover:border-ink-3",
      )}
    >
      <input type="file" accept={ACCEPTED.join(",")} className="sr-only" disabled={disabled} onChange={onChange} />
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={preview}
          alt={`Vista previa de ${file?.name}`}
          className={cx("max-h-[32rem] w-full object-contain", disabled && "opacity-60")}
          onLoad={(e) => onImage({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        />
      ) : file ? (
        <>
          <Icon name="check" className="mb-4 size-8 text-auto" />
          <span className="font-display text-lg text-ink">{file.name}</span>
          <span className="mt-1 text-sm text-ink-3">PDF listo para leer</span>
        </>
      ) : (
        <>
          <Icon name="upload" className="mb-4 size-9 text-gold" />
          <span className="font-display text-xl text-ink">Arrastre aquí la foto o el escaneo</span>
          <span className="mt-1.5 text-sm text-ink-2">o haga clic para elegir el archivo</span>
          <span className="mt-1 text-[0.8rem] text-ink-3">JPG, PNG o PDF, hasta 10 MB</span>
        </>
      )}
    </label>
  );
}

function CaptureTips({ limits }: { limits?: UploadLimits }) {
  const tips = [
    "El formato completo, con sus cuatro bordes a la vista",
    "Hoja plana, sin dobleces ni sombras",
    "Tomada de frente, sin inclinar la cámara",
    "Enfocada y con buena luz",
    limits ? `Al menos ${longSideFor(limits.recommended_dpi, limits).toLocaleString("es-PE")} px de lado largo` : "Buena resolución",
  ];
  return (
    <section aria-label="Recomendaciones para la captura" className="border border-line bg-surface p-6">
      <h2 className="font-display text-xl font-medium">Para una buena lectura</h2>
      <p className="mt-1 text-sm leading-relaxed text-ink-2">Una buena captura reduce los campos que tendrá que revisar.</p>
      <ul className="mt-5 space-y-3 text-sm text-ink">
        {tips.map((t) => (
          <li key={t} className="flex gap-2.5">
            <Icon name="check" className="mt-0.5 text-gold-ink" />
            {t}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Ready({ file, resolution, limits, user, setUser, model, setModel, meta, setMeta, onClear, onSubmit }: {
  file: File;
  resolution: Resolution | null;
  limits?: UploadLimits;
  user: string;
  setUser: (v: string) => void;
  model: string;
  setModel: (v: string) => void;
  meta: Meta;
  setMeta: (m: Meta) => void;
  onClear: () => void;
  onSubmit: () => void;
}) {
  return (
    <section aria-label="Archivo listo para leer" className="border border-line bg-surface p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="truncate font-display text-xl font-medium" title={file.name}>{file.name}</h2>
          <p className="mt-1 text-sm text-ink-3">{(file.size / 1e6).toFixed(1)} MB</p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClear}>Quitar</Button>
      </div>

      {resolution && limits && <div className="mt-4"><ResolutionNote resolution={resolution} limits={limits} /></div>}

      <Button onClick={onSubmit} disabled={resolution?.level === "rejected"} className="mt-5 w-full">
        Reconocer formato
      </Button>

      <details className="group mt-5 border-t border-line pt-4">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 text-sm text-ink-2 hover:text-ink">
          <Icon name="chevronRight" className="size-3.5 transition-transform group-open:rotate-90" />
          Más opciones
        </summary>
        <div className="mt-4 space-y-4">
          <div>
            <Label htmlFor="model">Modelo para leer</Label>
            <ModelSelect id="model" value={model} onChange={setModel} />
          </div>
          <div>
            <Label htmlFor="user">Usuario que carga</Label>
            <input id="user" className={inputClass} value={user} onChange={(e) => setUser(e.target.value)} />
          </div>
          <fieldset>
            <legend className="mb-1.5 text-sm text-ink-2">Fecha, guardia y equipo</legend>
            <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
              <input type="date" aria-label="Fecha" className={inputClass} value={meta.fecha} onChange={(e) => setMeta({ ...meta, fecha: e.target.value })} />
              <select aria-label="Guardia" className={inputClass} value={meta.guardia} onChange={(e) => setMeta({ ...meta, guardia: e.target.value })}>
                <option value="">Guardia</option>
                <option>A</option>
                <option>B</option>
                <option>C</option>
              </select>
              <input aria-label="Equipo" placeholder="Equipo" className={inputClass} value={meta.equipo} onChange={(e) => setMeta({ ...meta, equipo: e.target.value })} />
            </div>
            <p className="mt-1.5 text-[0.8rem] text-ink-3">Con los tres datos, el sistema avisa si el reporte ya se cargó.</p>
          </fieldset>
        </div>
      </details>
    </section>
  );
}

function ResolutionNote({ resolution, limits }: { resolution: Resolution; limits: UploadLimits }) {
  const dpi = Math.round(resolution.dpi);
  if (resolution.level === "rejected") {
    return <ErrorBox>Resolución muy baja ({dpi} ppp). Se necesita al menos {longSideFor(limits.min_dpi, limits).toLocaleString("es-PE")} px de lado largo; use una foto o un escaneo de mayor calidad.</ErrorBox>;
  }
  if (resolution.level === "low") {
    return <Notice tone="amber">Baja resolución ({dpi} ppp). Se puede leer, pero todos los campos pasarán a revisión.</Notice>;
  }
  return <Notice tone="emerald">Resolución adecuada ({dpi} ppp).</Notice>;
}

function Processing({ label }: { label: string }) {
  const [seconds, setSeconds] = useState(0);
  useInterval(() => setSeconds((s) => s + 1), 1000);
  return (
    <section className="border border-line bg-surface p-6" aria-live="polite">
      <div className="flex items-center gap-3 font-display text-xl">
        <Spinner className="size-5 shrink-0 text-gold" />
        <span className="min-w-0 truncate" title={label}>Leyendo {label}</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">
        <span className="tabular-nums">{seconds} s</span>. Al terminar se abre el documento. La primera lectura tarda más porque carga el modelo.
      </p>
      <ol className="mt-5 space-y-3 border-l-2 border-gold-tint pl-5 text-sm text-ink-2">
        {STEPS.map((s) => <li key={s}>{s}</li>)}
      </ol>
    </section>
  );
}

function Samples({ onProcess, disabled }: { onProcess: (s: Sample) => void; disabled: boolean }) {
  const { data, error, reload } = useApi<{ available: boolean; samples: Sample[] }>("/api/samples");
  const [n, setN] = useState(3);
  const [gen, setGen] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  async function generate() {
    setGen(true);
    setGenError(null);
    try {
      await post("/api/samples/generate", { n });
      reload();
    } catch (e) {
      setGenError(errorText(e));
    } finally {
      setGen(false);
    }
  }

  if (error && !data) return <div className="mt-10"><SetupHelp error={error} /></div>;
  if (!data?.available) return null;

  const samples = data.samples;
  const pending = samples.filter((s) => !s.document).length;
  return (
    <details className="group mt-10 border-t border-line pt-5">
      <summary className="flex cursor-pointer list-none items-center gap-2 hover:text-ink">
        <Icon name="chevronRight" className="size-4 transition-transform group-open:rotate-90" />
        <span className="font-display text-xl font-medium">Probar con un formato de prueba</span>
        <span className="text-sm text-ink-3">{pending} sin procesar</span>
      </summary>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-xl text-sm leading-relaxed text-ink-2">
          Con verdad conocida, para comparar la lectura. Los formatos demo son nuevos: ningún modelo los vio al entrenar.
        </p>
        <div className="flex items-center gap-2">
          <select aria-label="Cantidad de formatos a generar" className={cx(inputClass, "h-8 w-16 px-2")} value={n} onChange={(e) => setN(Number(e.target.value))}>
            {[1, 2, 3, 4, 6, 8].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <Button size="sm" variant="secondary" onClick={generate} busy={gen} disabled={disabled}>
            Generar formatos nuevos
          </Button>
        </div>
      </div>
      {genError && <div className="mt-4"><ErrorBox>{genError}</ErrorBox></div>}
      <ul className="mt-5 grid grid-cols-2 gap-px border border-line bg-line sm:grid-cols-3 lg:grid-cols-4">
        {[...samples].reverse().map((s) => {
          const body = (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={sampleImageUrl(s.sample_id, 360)} alt="" loading="lazy" className="aspect-[1.414] w-full bg-paper object-cover" />
              <div className="flex flex-1 flex-col gap-2 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium" title={s.sample_id}>{s.sample_id}</span>
                  <span className="shrink-0 text-[0.8rem] text-ink-3">{BATCH_LABEL[s.batch] ?? s.batch}</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {s.new_operator && <Badge title="Su letra no aparece en el entrenamiento">Operador nuevo</Badge>}
                  {s.rescan && <Badge title="Debe detectarse como duplicado">Reescaneo</Badge>}
                  {s.bad_scan && <Badge title="Debe rechazarse en el control de calidad">Escaneo defectuoso</Badge>}
                </div>
                <div className="mt-auto pt-1">{s.document && <StateBadge state={s.document.state} />}</div>
              </div>
            </>
          );
          return (
            <li key={s.sample_id} className="flex flex-col bg-surface">
              {s.document ? (
                <Link href={`/documentos/${s.document.id}`} className="flex flex-1 flex-col hover:bg-paper-2" aria-label={`Abrir ${s.sample_id}`}>{body}</Link>
              ) : (
                <div className="flex flex-1 flex-col">
                  {body}
                  <div className="px-3 pb-3">
                    <Button size="sm" variant="secondary" className="w-full" disabled={disabled} onClick={() => onProcess(s)}>
                      Procesar
                    </Button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
