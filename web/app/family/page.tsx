import type { Metadata } from "next";
import { FamilyApp } from "@/components/family/FamilyApp";

export const metadata: Metadata = {
  title: "Family dashboard · ScamShield",
  description: "What was checked, who was alerted, and how ScamShield decided.",
  robots: { index: false },
};

export default function FamilyPage() {
  return <FamilyApp />;
}
