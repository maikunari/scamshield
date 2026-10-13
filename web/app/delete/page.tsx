import type { Metadata } from "next";
import { DeleteData } from "@/components/DeleteData";

export const metadata: Metadata = { title: "Delete my data - ScamShield" };

export default function DeletePage() {
  return <DeleteData />;
}
