import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 24 24", fill: "none", "aria-hidden": true } as const;

export function ShieldIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M12 2.5 20 5.5v6.2c0 4.7-3.3 8.3-8 9.8-4.7-1.5-8-5.1-8-9.8V5.5l8-3Z" fill="currentColor" opacity=".18" />
      <path d="M12 2.5 20 5.5v6.2c0 4.7-3.3 8.3-8 9.8-4.7-1.5-8-5.1-8-9.8V5.5l8-3Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="m8.5 12.2 2.4 2.4 4.8-5.2" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SearchIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" strokeWidth="2.4" />
      <path d="m15.5 15.5 5 5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

export function SpeakerIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4Z" fill="currentColor" />
      <path d="M15.5 8.8a4.5 4.5 0 0 1 0 6.4M18.3 6a8.3 8.3 0 0 1 0 12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

export function StopIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
    </svg>
  );
}

export function BackIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M14.5 5.5 8 12l6.5 6.5" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function LockIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <rect x="5" y="10.5" width="14" height="10" rx="2.5" stroke="currentColor" strokeWidth="2" />
      <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

export function ExternalIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M14 5h5v5M19 5l-8 8M10 6H6.5A1.5 1.5 0 0 0 5 7.5v10A1.5 1.5 0 0 0 6.5 19h10a1.5 1.5 0 0 0 1.5-1.5V14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function FlagDot(props: P) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
      <path d="M12 7.5v5.5" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="12" cy="16.5" r="1.4" fill="currentColor" />
    </svg>
  );
}

/** The big verdict mark: cross, exclamation or tick in a ring. */
export function VerdictMark({ kind, ...props }: P & { kind: "red" | "yellow" | "green" }) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeWidth="1.6" opacity=".55" />
      <circle cx="12" cy="12" r="8.6" fill="currentColor" opacity=".16" />
      {kind === "red" && <path d="m8.2 8.2 7.6 7.6M15.8 8.2l-7.6 7.6" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />}
      {kind === "yellow" && (
        <>
          <path d="M12 6.8v6" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
          <circle cx="12" cy="16.6" r="1.6" fill="currentColor" />
        </>
      )}
      {kind === "green" && <path d="m7.4 12.4 3 3 6.2-6.6" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />}
    </svg>
  );
}
