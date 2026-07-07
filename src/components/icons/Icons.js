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

export function ClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.2 2" />
    </svg>
  );
}

export function SearchOffIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M15.2 15.2 21 21" />
      <path d="M8 13l5-5" />
    </svg>
  );
}

export function DocumentIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h6" />
    </svg>
  );
}

export function ScaleIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3v18M6 8l-3.5 7a3.5 3.5 0 0 0 7 0L6 8Zm12 0l-3.5 7a3.5 3.5 0 0 0 7 0L18 8Z" />
      <path d="M4 8h4M16 8h4M8 21h8" />
    </svg>
  );
}

export function BlockIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <path d="M8 8l8 8" />
    </svg>
  );
}

export function LockClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      <path d="M12 15v2.5" />
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

export function ChartIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 20V10M12 20V4M20 20v-7" />
      <path d="M2 20h20" />
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

export function EyeOffIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M3 3l18 18" />
      <path d="M10.6 5.2A10.6 10.6 0 0 1 12 5c5 0 8.7 3.4 10 7-0.5 1.4-1.4 2.9-2.6 4.1M6.6 6.6C4.4 8 2.8 10 2 12c1.3 3.6 5 7 10 7 1.5 0 2.9-0.3 4.1-0.9" />
      <path d="M9.5 9.9a3.2 3.2 0 0 0 4.6 4.5" />
    </svg>
  );
}

export function ArrowRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 12h16" />
      <path d="M13 5l7 7-7 7" />
    </svg>
  );
}

export function EthIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path fill="currentColor" d="M12 1.5 5.5 12.6 12 16l6.5-3.4L12 1.5Z" opacity="0.75" />
      <path fill="currentColor" d="M12 17.3 5.5 13.9 12 22.5l6.5-8.6-6.5 3.4Z" />
    </svg>
  );
}

export function BtcIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <circle cx="12" cy="12" r="10.5" fill="currentColor" opacity="0.16" />
      <path
        fill="currentColor"
        d="M11.2 6.4h1.5v1.5c1.6.2 2.9 1 2.9 2.6 0 .9-.5 1.6-1.2 2 .9.3 1.6 1.1 1.6 2.3 0 1.9-1.5 2.7-3.3 2.9v1.5h-1.5v-1.5h-.9v1.5H8.9v-1.5H6.8v-1.5h1c.4 0 .6-.2.6-.6v-5.7c0-.4-.2-.6-.6-.6h-1V8.1h2.1V6.4h1.5v1.5h.9V6.4Zm-.4 3.1v2.1h1.3c.8 0 1.4-.4 1.4-1.1 0-.7-.6-1-1.4-1h-1.3Zm0 3.5v2.3h1.5c.9 0 1.6-.4 1.6-1.2 0-.8-.7-1.1-1.6-1.1h-1.5Z"
      />
    </svg>
  );
}

export function TronIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path fill="currentColor" d="M3 4.8 13.6 3l7.4 6.8-9.4 11.2L3 4.8Zm3 .9 8 12.4 6.2-9.5-5.4-5-8.8 2.1Z" />
    </svg>
  );
}

export function VisaIcon(props) {
  return (
    <svg viewBox="0 0 36 22" width="36" height="22" {...props}>
      <rect width="36" height="22" rx="4" fill="currentColor" opacity="0.12" />
      <text x="18" y="15" textAnchor="middle" fontSize="9" fontWeight="700" fontFamily="Arial, sans-serif" fill="currentColor">
        VISA
      </text>
    </svg>
  );
}

export function MastercardIcon(props) {
  return (
    <svg viewBox="0 0 36 22" width="36" height="22" {...props}>
      <rect width="36" height="22" rx="4" fill="currentColor" opacity="0.12" />
      <circle cx="15" cy="11" r="6" fill="currentColor" opacity="0.55" />
      <circle cx="22" cy="11" r="6" fill="currentColor" opacity="0.35" />
    </svg>
  );
}

export function MirIcon(props) {
  return (
    <svg viewBox="0 0 36 22" width="36" height="22" {...props}>
      <rect width="36" height="22" rx="4" fill="currentColor" opacity="0.12" />
      <text x="18" y="15" textAnchor="middle" fontSize="8" fontWeight="700" fontFamily="Arial, sans-serif" fill="currentColor">
        MIR
      </text>
    </svg>
  );
}

export function CryptoIcon(props) {
  return (
    <svg viewBox="0 0 36 22" width="36" height="22" {...props}>
      <rect width="36" height="22" rx="4" fill="currentColor" opacity="0.12" />
      <path fill="currentColor" d="M18 5l4.5 3v6L18 17l-4.5-3V8L18 5Z" opacity="0.7" />
    </svg>
  );
}

export function TelegramIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path
        fill="currentColor"
        d="M21.5 3.5 2.8 10.9c-1.1.4-1.1 1 0 1.4l4.8 1.5 1.9 5.9c.2.6.5.7 1 .3l2.6-2.1 4.7 3.5c.6.4 1.1.2 1.3-.6l3.6-16.6c.2-.8-.3-1.2-1.2-.7Z"
      />
    </svg>
  );
}

export function WhatsappIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path
        fill="currentColor"
        d="M12 2.5A9.5 9.5 0 0 0 3.6 16.9L2.5 21.5l4.7-1.2A9.5 9.5 0 1 0 12 2.5Zm5.4 13.5c-.2.6-1.3 1.2-1.9 1.3-.5.1-1.1.1-1.8-.1-.4-.1-1-.3-1.7-.6-2.9-1.3-4.8-4.2-4.9-4.4-.1-.2-1.2-1.6-1.2-3 0-1.4.7-2.1 1-2.4.3-.3.6-.4.8-.4h.6c.2 0 .4 0 .6.5.2.5.7 1.8.8 1.9.1.2.1.3 0 .5-.1.2-.2.3-.3.5-.2.2-.3.3-.5.5-.2.2-.3.4-.1.7.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.4 2.4 1.5.3.1.4.1.6-.1.2-.2.7-.8.9-1.1.2-.3.4-.2.6-.1.2.1 1.5.7 1.8.8.3.1.4.2.5.3.1.2.1.7-.1 1.3Z"
      />
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

export function VkIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path
        fill="currentColor"
        d="M13.4 17.4c-5.3 0-8.4-3.7-8.5-9.8h2.7c.1 4.4 2 6.3 3.5 6.7V7.6h2.5v3.9c1.5-.2 3-1.9 3.6-3.9h2.5c-.4 2.5-2.2 4.2-3.5 4.9 1.3.6 3.3 2.1 4.1 4.9h-2.8c-.6-2-2.4-3.6-4.4-3.8v3.8h-.3Z"
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

export function ChevronDownIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m6 9 6 6 6-6" />
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

export function AccountIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <circle cx="17.5" cy="7" r="2.2" />
      <path d="M15 12.3a4.2 4.2 0 0 1 6 3.9" />
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

export function RatingIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="8" r="5" />
      <path d="M8.5 12.5 7 21l5-2.5 5 2.5-1.5-8.5" />
    </svg>
  );
}

export function AwardIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m12 2 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4-3.9-3.8 5.4-.8Z" />
    </svg>
  );
}

export function TagIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M3 11.5V5a2 2 0 0 1 2-2h6.5a2 2 0 0 1 1.4.6l8 8a2 2 0 0 1 0 2.8l-6.6 6.6a2 2 0 0 1-2.8 0l-8-8a2 2 0 0 1-.5-1.5Z" />
      <circle cx="8" cy="8" r="1.4" fill="currentColor" stroke="none" />
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

export function RefreshIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 12a8 8 0 0 1 13.7-5.7L20 8.5" />
      <path d="M20 4v4.5h-4.5" />
      <path d="M20 12a8 8 0 0 1-13.7 5.7L4 15.5" />
      <path d="M4 20v-4.5h4.5" />
    </svg>
  );
}

export function HelpCircleIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.2 9.3a2.8 2.8 0 1 1 4.3 2.4c-.8.5-1.5 1-1.5 2.1" />
      <path d="M12 17.2v.1" />
    </svg>
  );
}

export function SortIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m7 8 3-4 3 4" />
      <path d="m7 16 3 4 3-4" />
    </svg>
  );
}

export function IncomingIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M12 3v8" />
      <path d="m8.5 8 3.5 3.5L15.5 8" />
    </svg>
  );
}

export function OutgoingIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M12 11V3" />
      <path d="m8.5 6 3.5-3.5L15.5 6" />
    </svg>
  );
}

export function PercentIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M5 19 19 5" />
      <circle cx="7" cy="7" r="2.3" />
      <circle cx="17" cy="17" r="2.3" />
    </svg>
  );
}

export function ChatBubbleIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z" />
    </svg>
  );
}

export function ProfileIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="9" r="4" />
      <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
    </svg>
  );
}

export function EditIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 20h4l10.5-10.5a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4 16v4Z" />
      <path d="M13.5 6.5l4 4" />
    </svg>
  );
}

export function WalletGlyphIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" {...props}>
      <circle cx="12" cy="12" r="11" fill="currentColor" />
      <path stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none" d="M12 17V7M8 11l4-4 4 4" />
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

export function PlayIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 8.5v7l6-3.5Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function UsersGroupIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="8.5" cy="8" r="2.8" />
      <circle cx="16" cy="9" r="2.2" />
      <path d="M3 20a5.5 5.5 0 0 1 11 0" />
      <path d="M14.5 14.4a4.3 4.3 0 0 1 6 3.9" />
    </svg>
  );
}
