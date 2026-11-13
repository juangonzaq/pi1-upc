import type { Metadata } from "next";
import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { DocumentList } from "./document-list";

export const metadata: Metadata = { title: "Documentos y revisión" };

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <DocumentList />
    </Suspense>
  );
}
