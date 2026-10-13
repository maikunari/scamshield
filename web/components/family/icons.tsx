import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 24 24", fill: "none", "aria-hidden": true } as const;
const stroke = { stroke: "currentColor", strokeWidth: 1.9, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export function MailIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.2" {...stroke} />
      <path d="m4.5 7.5 7.5 5.5 7.5-5.5" {...stroke} />
    </svg>
  );
}

export function SendIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M20.5 4.2 3.6 10.6c-.8.3-.8 1.4 0 1.7l4.2 1.5 1.6 5c.2.8 1.2 1 1.7.4l2.4-2.6 4.1 3c.6.4 1.4.1 1.6-.6l2.6-14.2c.2-.9-.7-1.5-1.5-1.1Z" {...stroke} />
      <path d="m7.8 13.8 9.4-6.3" {...stroke} />
    </svg>
  );
}

export function PhoneIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M6.6 3.8h3l1.3 3.6-1.9 1.3a11 11 0 0 0 5.3 5.3l1.3-1.9 3.6 1.3v3a2 2 0 0 1-2.1 2A14.8 14.8 0 0 1 4.6 5.9a2 2 0 0 1 2-2.1Z" {...stroke} />
    </svg>
  );
}

export function GlobeIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" {...stroke} />
      <path d="M3.8 12h16.4M12 3.5c2.4 2.3 3.6 5.1 3.6 8.5s-1.2 6.2-3.6 8.5c-2.4-2.3-3.6-5.1-3.6-8.5S9.6 5.8 12 3.5Z" {...stroke} />
    </svg>
  );
}

export function PlusIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M12 5v14M5 12h14" {...stroke} strokeWidth={2.2} />
    </svg>
  );
}

export function PencilIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="m4.5 19.5.8-3.9L16.6 4.3a1.9 1.9 0 0 1 2.7 0l.4.4a1.9 1.9 0 0 1 0 2.7L8.4 18.7l-3.9.8Z" {...stroke} />
      <path d="m14.8 6.2 3 3" {...stroke} />
    </svg>
  );
}

export function TrashIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M4.5 7h15M9.5 7V4.8h5V7M6.5 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-12" {...stroke} />
    </svg>
  );
}

export function CopyIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <rect x="8.5" y="8.5" width="11" height="11" rx="2" {...stroke} />
      <path d="M15.5 8.5V6.2a1.7 1.7 0 0 0-1.7-1.7H6.2a1.7 1.7 0 0 0-1.7 1.7v7.6a1.7 1.7 0 0 0 1.7 1.7h2.3" {...stroke} />
    </svg>
  );
}

export function CheckIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="m5.5 12.6 4.2 4.2 8.8-9.2" {...stroke} strokeWidth={2.4} />
    </svg>
  );
}

export function RefreshIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.3-5.4M19.5 4.5v4h-4" {...stroke} />
    </svg>
  );
}

export function ChevronIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="m9.5 5.5 6.5 6.5-6.5 6.5" {...stroke} strokeWidth={2.2} />
    </svg>
  );
}

export function ClockIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" {...stroke} />
      <path d="M12 7.5V12l3 2" {...stroke} />
    </svg>
  );
}

export function ChipIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <rect x="6.5" y="6.5" width="11" height="11" rx="2" {...stroke} />
      <rect x="9.7" y="9.7" width="4.6" height="4.6" rx=".8" fill="currentColor" opacity=".25" />
      <path d="M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3" {...stroke} />
    </svg>
  );
}

export function BookIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M5 5.2c2.4-.8 4.7-.6 7 1v13c-2.3-1.6-4.6-1.8-7-1V5.2ZM19 5.2c-2.4-.8-4.7-.6-7 1v13c2.3-1.6 4.6-1.8 7-1V5.2Z" {...stroke} />
    </svg>
  );
}

export function BellIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M6.2 16.5V11a5.8 5.8 0 0 1 11.6 0v5.5l1.4 1.6H4.8l1.4-1.6ZM10 20a2.2 2.2 0 0 0 4 0" {...stroke} />
    </svg>
  );
}

export function ListIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <path d="M8.5 6.5h11M8.5 12h11M8.5 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" {...stroke} strokeWidth={2.4} />
    </svg>
  );
}

export function UserIcon(props: P) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="8.3" r="3.6" {...stroke} />
      <path d="M5 19.5c.7-3.4 3.4-5.2 7-5.2s6.3 1.8 7 5.2" {...stroke} />
    </svg>
  );
}

export { ExternalIcon } from "../Icons";
