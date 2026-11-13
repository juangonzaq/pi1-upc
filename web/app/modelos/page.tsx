import type { Metadata } from "next";
import { Models } from "./models";

export const metadata: Metadata = { title: "Modelos y reentrenamiento" };

export default function Page() {
  return <Models />;
}
