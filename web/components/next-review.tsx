"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { errorText, nextToReview } from "@/lib/api";
import { Button } from "./ui";

/** Abre el formato más antiguo que espera revisión; si no hay, lo dice en lugar de navegar. */
export function NextReviewButton({ except, label = "Revisar el siguiente formato", variant = "primary" }: {
  except?: string;
  label?: string;
  variant?: "primary" | "secondary" | "dark";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setMsg(null);
    try {
      const id = await nextToReview(except);
      if (id) router.push(`/documentos/${id}`);
      else setMsg("No quedan formatos por revisar.");
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-3">
      <Button variant={variant} onClick={go} busy={busy}>
        {label}
      </Button>
      {msg && <span className="text-sm text-ink-3">{msg}</span>}
    </span>
  );
}
