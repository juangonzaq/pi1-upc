import type { Metadata } from "next";
import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { DocumentView } from "./document-view";

export const metadata: Metadata = { title: "Documento" };

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <DocumentView />
    </Suspense>
  );
}
