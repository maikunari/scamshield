import { CheckApp } from "@/components/CheckApp";

// `/` and `/check/` are the same screen; `/check/` is the installed PWA's start URL and share target.
export default function HomePage() {
  return <CheckApp />;
}
