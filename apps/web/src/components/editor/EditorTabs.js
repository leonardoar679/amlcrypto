import Link from 'next/link';

// What the platform's editors write: the incidents and the wallet profiles. One place to move between
// them (the permission is the same, so is the gate).
export default function EditorTabs({ active }) {
  const tab = (key, href, label) => (
    <Link className={`filter-link${active === key ? ' is-active' : ''}`} href={href} aria-current={active === key ? 'page' : undefined}>
      {label}
    </Link>
  );
  return (
    <nav className="editor-tabs" aria-label="What to edit">
      {tab('incidents', '/editor/incidents', 'Incidents')}
      {tab('wallets', '/editor/wallets', 'Wallet profiles')}
    </nav>
  );
}
