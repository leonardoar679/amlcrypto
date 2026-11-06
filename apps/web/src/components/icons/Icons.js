const base = {
  width: 22,
  height: 22,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round'
};

export function DocumentIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h6" />
    </svg>
  );
}

export function ShieldIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3l7 3v6c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6l7-3Z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

export function LayersIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3 3 8l9 5 9-5-9-5Z" />
      <path d="M3 12l9 5 9-5M3 16l9 5 9-5" />
    </svg>
  );
}

export function UsersIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.5a3.2 3.2 0 0 1 0 6.2M20.5 20a5 5 0 0 0-4.5-5.6" />
    </svg>
  );
}

export function CheckCircleIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 12.3l2.4 2.4 4.6-5.2" />
    </svg>
  );
}

export function BoltIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />
    </svg>
  );
}

export function MailIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path
        fill="currentColor"
        d="M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm1.4 2 7.6 5.6L19.6 7H4.4ZM4 8.3V18h16V8.3l-8 5.9-8-5.9Z"
      />
    </svg>
  );
}

export function SearchIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function BellIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M6 10a6 6 0 0 1 12 0c0 4 1.5 5.5 2 6.5H4c.5-1 2-2.5 2-6.5Z" />
      <path d="M10 19.5a2.2 2.2 0 0 0 4 0" />
    </svg>
  );
}

export function MenuIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

export function AddressBookIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="4" y="3" width="14" height="18" rx="2" />
      <circle cx="11" cy="10" r="2.2" />
      <path d="M7.5 16a3.5 3.5 0 0 1 7 0" />
      <path d="M2 7h2M2 12h2M2 17h2" />
    </svg>
  );
}

export function HistoryIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <path d="M3 4v4h4" />
      <path d="M12 8v4l3 2" />
    </svg>
  );
}

export function SettingsIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3H9.8l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9c.6.5 1.3.9 2 1.2l.4 2.6h4.4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z" />
    </svg>
  );
}

export function LogoutIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

export function CopyIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M6 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

export function GraphNodesIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="5" r="2" />
      <circle cx="18" cy="12" r="2" />
      <circle cx="18" cy="19" r="2" />
      <path d="M8.6 10.7 16 6M9 12h7M8.6 13.3 16 18" />
    </svg>
  );
}

export function TracePathIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="5" cy="6" r="2.3" />
      <circle cx="19" cy="18" r="2.3" />
      <path d="M7.2 7.2c2 1.6 2 3 1 4.8s.2 3.2 2.2 3.6 3-.4 3.6-1.6 2.4-1.7 3.7 0" strokeDasharray="1.5 3" />
    </svg>
  );
}

export function PersonQuestionIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M6 21v-1a5 5 0 0 1 5-5h1a5 5 0 0 1 5 5v1" />
      <path d="M9.8 8.3a2.2 2.2 0 1 1 3 2c-.7.5-1 .9-1 1.7" />
      <path d="M11.8 14.3v.1" />
    </svg>
  );
}

export function BitcoinFlowIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M10.7 9.8h2.1c.7 0 1.2.4 1.2 1s-.5 1-1.2 1h-2.1m0 0h2.3c.8 0 1.3.4 1.3 1.1s-.5 1.1-1.3 1.1h-2.3m1-4.2v-1m0 5.2v1m1.6-6.2v-1m0 5.2v1" />
      <path d="M2 6h3M2 12h2.2M2 18h3M19 6h3M21.8 12H22M19 18h3" />
    </svg>
  );
}

export function GroupShapesIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="6" cy="6" r="2" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="18" cy="6" r="2" />
      <path d="M6 13.5 8.3 17H3.7Z" />
      <path d="m12 13.5 2.3 3.5H9.7Z" />
      <path d="m18 13.5 2.3 3.5h-4.6Z" />
    </svg>
  );
}
