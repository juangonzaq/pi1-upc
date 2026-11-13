import type { Metadata } from "next";
import { Recognize } from "./recognize";

export const metadata: Metadata = { title: "Reconocer formato" };

export default function Page() {
  return <Recognize />;
}
