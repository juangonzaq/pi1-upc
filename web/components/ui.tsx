import type { ButtonHTMLAttributes, ReactNode } from "react";
import { STATE_LABEL } from "@/lib/format";
import type { DocState, Route } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ íconos (trazo de 1,75 px, 16 px)
const ICONS = {
  check: "M3.5 8.5l3 3 6-7",
  review: "M8 4.5v4.25M8 11.25v.25M2.5 13.5h11L8 2.5z",
  decided: "M10.75 2.75l2.5 2.5L6 12.5H3.5V10z",
  error: "M4.5 4.5l7 7M11.5 4.5l-7 7",
  neutral: "M8 8h.01",
  upload: "M8 10.5V2.75M5 5.5l3-3 3 3M2.75 10.5v2.75h10.5V10.5",
  chevronLeft: "M10 3.5L5.5 8l4.5 4.5",
  chevronRight: "M6 3.5L10.5 8 6 12.5",
  search: "M7 12a5 5 0 100-10 5 5 0 000 10zM10.75 10.75L14 14",
  refresh: "M13 8a5 5 0 11-1.5-3.55M13 2.5v2.75h-2.75",
} as const;

export function Icon({ name, className }: { name: keyof typeof ICONS; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={cx("size-4 shrink-0", className)} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={ICONS[name]} />
    </svg>
  );
}

// ------------------------------------------------------------------ estructura
export function PageHeader({ title, description, actions, kicker }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; kicker?: ReactNode }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4 border-b border-line pb-6">
      <div className="min-w-0 max-w-3xl">
        {kicker && <div className="mb-2 text-sm text-ink-3">{kicker}</div>}
        <h1 className="font-display text-[2rem] font-medium leading-tight tracking-[-0.01em] text-ink">{title}</h1>
        {description && <div className="mt-2 text-[0.95rem] leading-relaxed text-ink-2">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({ title, subtitle, actions, children, className, padded = true }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={cx("border border-line bg-surface", className)}>
      {(title || actions) && (
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="font-display text-[1.05rem] font-medium text-ink">{title}</h2>}
            {subtitle && <p className="mt-1 text-sm leading-snug text-ink-3">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
        </div>
      )}
      <div className={padded ? "p-5" : undefined}>{children}</div>
    </section>
  );
}

export const Panel = Card;

// ------------------------------------------------------------------ acciones
type Variant = "primary" | "dark" | "secondary" | "ghost" | "danger" | "success";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-gold text-ink hover:bg-[#b08a2f] disabled:opacity-45 disabled:hover:bg-gold",
  success: "bg-gold text-ink hover:bg-[#b08a2f] disabled:opacity-45 disabled:hover:bg-gold",
  dark: "bg-ink text-white hover:bg-[#3a3633] disabled:opacity-45 disabled:hover:bg-ink",
  secondary: "border border-ink/25 bg-surface text-ink hover:border-ink disabled:border-line disabled:text-ink-3",
  ghost: "text-ink-2 hover:bg-paper hover:text-ink disabled:text-ink-3",
  danger: "border border-error/40 bg-surface text-error hover:border-error hover:bg-error-tint disabled:text-ink-3",
};

export function Button({ variant = "primary", size = "md", busy, children, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "h-8 px-3 text-[0.82rem]" : "h-10 px-4 text-sm",
        VARIANTS[variant],
        className,
      )}
    >
      {busy && <Spinner className="size-3.5" />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className ?? "size-4")} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="inline-flex h-5 min-w-5 items-center justify-center border border-line bg-paper-2 px-1 font-sans text-[0.7rem] text-ink-2">{children}</kbd>;
}

// ------------------------------------------------------------------ estados (color + ícono + texto, nunca color solo)
export type Status = "auto" | "review" | "decided" | "error" | "neutral" | "gold";
const STATUS_STYLE: Record<Status, { box: string; icon: string; glyph: keyof typeof ICONS }> = {
  auto: { box: "bg-auto-tint", icon: "text-auto", glyph: "check" },
  review: { box: "bg-review-tint", icon: "text-review", glyph: "review" },
  decided: { box: "bg-decided-tint", icon: "text-decided", glyph: "decided" },
  error: { box: "bg-error-tint", icon: "text-error", glyph: "error" },
  neutral: { box: "bg-paper", icon: "text-ink-3", glyph: "neutral" },
  gold: { box: "bg-gold-tint", icon: "text-gold-ink", glyph: "check" },
};

export function StatusPill({ status, children, title, className }: { status: Status; children: ReactNode; title?: string; className?: string }) {
  const s = STATUS_STYLE[status];
  return (
    <span title={title} className={cx("inline-flex h-6 items-center gap-1.5 whitespace-nowrap px-2 text-[0.8rem] text-ink", s.box, className)}>
      <Icon name={s.glyph} className={cx("size-3.5", s.icon)} />
      {children}
    </span>
  );
}

type Tone = "slate" | "emerald" | "amber" | "rose" | "sky" | "violet" | "gold";
const TONE_STATUS: Record<Tone, Status> = { slate: "neutral", sky: "neutral", emerald: "auto", amber: "review", rose: "error", violet: "decided", gold: "gold" };

/** Etiqueta neutra (sin ícono) para categorías que no son estados. */
export function Badge({ tone = "slate", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  const status = TONE_STATUS[tone];
  if (status !== "neutral" && status !== "gold") {
    return <StatusPill status={status} title={title} className={className}>{children}</StatusPill>;
  }
  return (
    <span title={title} className={cx("inline-flex h-6 items-center whitespace-nowrap px-2 text-[0.8rem]", status === "gold" ? "bg-gold-tint text-gold-ink" : "bg-paper text-ink-2", className)}>
      {children}
    </span>
  );
}

export const STATE_STATUS: Record<DocState, Status> = {
  CARGADO: "neutral",
  EN_COLA: "neutral",
  PROCESADO: "neutral",
  EN_REVISION: "review",
  VALIDADO: "decided",
  APROBADO: "auto",
  ERROR: "error",
  DUPLICADO_PENDIENTE: "error",
};

export function StateBadge({ state }: { state: DocState }) {
  return <StatusPill status={STATE_STATUS[state]}>{STATE_LABEL[state]}</StatusPill>;
}

export function RouteBadge({ route }: { route: Route }) {
  return route === "AUTO" ? (
    <StatusPill status="auto" title="Confianza calibrada sobre el umbral y sin reglas violadas">Automático</StatusPill>
  ) : (
    <StatusPill status="review" title="Requiere decisión del validador">A revisión</StatusPill>
  );
}

export function TruthMark({ correct, yes = "coincide", no = "no coincide" }: { correct: boolean | null; yes?: string; no?: string }) {
  if (correct === null) return null;
  return correct ? (
    <span className="inline-flex items-center gap-1 text-[0.8rem] text-ink-2"><Icon name="check" className="size-3.5 text-auto" />{yes}</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[0.8rem] text-ink-2"><Icon name="error" className="size-3.5 text-error" />{no}</span>
  );
}

// ------------------------------------------------------------------ medidas
/** Proporción sobre un riel del mismo tono, más claro (skill dataviz: meter). */
export function Meter({ value, status = "gold", className, label }: { value: number; status?: Status; className?: string; label?: string }) {
  const fill = { auto: "bg-auto", review: "bg-review", decided: "bg-decided", error: "bg-error", neutral: "bg-ink-3", gold: "bg-gold" }[status];
  const track = { auto: "bg-auto-tint", review: "bg-review-tint", decided: "bg-decided-tint", error: "bg-error-tint", neutral: "bg-paper", gold: "bg-gold-tint" }[status];
  return (
    <div className={cx("h-1.5 w-full overflow-hidden", track, className)} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)} aria-label={label}>
      <div className={cx("h-full", fill)} style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
    </div>
  );
}

/** Confianza calibrada con la marca del umbral del tipo de campo. */
export function ConfidenceBar({ value, threshold, reliable = true, wide }: { value: number | null; threshold?: number | null; reliable?: boolean | null; wide?: boolean }) {
  if (value === null) return <span className="text-sm text-ink-3">sin lectura</span>;
  const above = threshold !== null && threshold !== undefined && value >= threshold && reliable !== false;
  return (
    <div className="flex items-center gap-3" title={threshold != null ? `Umbral calibrado ${threshold.toFixed(3)}` : "Sin umbral calibrado para este tipo de campo"}>
      <div className={cx("relative h-1.5 overflow-hidden", wide ? "w-40" : "w-20", above ? "bg-auto-tint" : "bg-review-tint")}>
        <div className={cx("h-full", above ? "bg-auto" : reliable === false ? "bg-silver" : "bg-review")} style={{ width: `${Math.max(2, value * 100)}%` }} />
        {threshold != null && <div className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: `${threshold * 100}%` }} />}
      </div>
      <span className="tabular text-sm text-ink">{value.toFixed(3)}</span>
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: "emerald" | "amber" | "rose" }) {
  const mark = tone === "emerald" ? "bg-auto" : tone === "amber" ? "bg-review" : tone === "rose" ? "bg-error" : "bg-line";
  return (
    <div className="relative bg-surface px-5 py-4">
      <span className={cx("absolute inset-y-4 left-0 w-0.5", mark)} aria-hidden />
      <div className="text-sm text-ink-3">{label}</div>
      <div className="mt-1 font-display text-[1.75rem] font-medium leading-none text-ink">{value}</div>
      {hint && <div className="mt-2 text-[0.8rem] leading-snug text-ink-3">{hint}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ mensajes
export function ErrorBox({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start justify-between gap-4 border-l-[3px] border-error bg-error-tint px-4 py-3 text-sm text-ink">
      <div className="flex min-w-0 gap-2.5">
        <Icon name="error" className="mt-0.5 text-error" />
        <div className="min-w-0 break-words">{children}</div>
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Reintentar
        </Button>
      )}
    </div>
  );
}

export function Notice({ tone = "sky", children }: { tone?: "sky" | "amber" | "emerald"; children: ReactNode }) {
  const t = tone === "amber" ? { b: "border-review bg-review-tint", i: "review" as const, c: "text-review" } : tone === "emerald" ? { b: "border-auto bg-auto-tint", i: "check" as const, c: "text-auto" } : { b: "border-gold bg-gold-tint", i: "neutral" as const, c: "text-gold-ink" };
  return (
    <div className={cx("flex gap-2.5 border-l-[3px] px-4 py-3 text-sm leading-relaxed text-ink", t.b)}>
      {tone !== "sky" && <Icon name={t.i} className={cx("mt-0.5", t.c)} />}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Loading({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-ink-3">
      <Spinner /> {label}
    </div>
  );
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-4 border border-dashed border-silver px-6 py-12 text-center text-sm text-ink-2">
      <div className="max-w-md leading-relaxed">{children}</div>
      {action}
    </div>
  );
}

// ------------------------------------------------------------------ formularios
export function Label({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-sm text-ink-2">
      {children}
    </label>
  );
}

export const inputClass =
  "h-10 w-full border border-ink/20 bg-surface px-3 text-sm text-ink placeholder:text-ink-3 focus:border-gold focus:outline-none focus:ring-2 focus:ring-gold/30";

export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex flex-wrap border border-line bg-surface">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.value)}
            className={cx("relative h-9 px-3.5 text-sm transition-colors", on ? "bg-ink text-white" : "text-ink-2 hover:bg-paper hover:text-ink")}
          >
            {o.label}
            {o.count !== undefined && <span className={cx("tabular ml-2", on ? "text-white/70" : "text-ink-3")}>{o.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
