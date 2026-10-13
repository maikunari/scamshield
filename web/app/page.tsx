import { Landing } from "@/components/family/Landing";

// `/` is the front door (the demo family for judges, links to both sides). The parent's own screen is `/check/`,
// which is also the installed PWA's start URL and share target.
export default function HomePage() {
  return <Landing />;
}
