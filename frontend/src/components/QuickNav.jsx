const SECTIONS = [
  { id: 'blotter', label: 'Blotter' },
  { id: 'ledger', label: 'Ledger' },
  { id: 'analyst', label: 'Analyst' },
  { id: 'vault', label: 'Vault' },
  { id: 'desk', label: 'Desk' },
];

export default function QuickNav({ isAdmin }) {
  const sections = isAdmin ? [...SECTIONS, { id: 'registry', label: 'Registry' }] : SECTIONS;
  return (
    <nav className="quicknav" aria-label="Jump to section">
      {sections.map((s) => (
        <a key={s.id} className="quicknav__pill" href={`#${s.id}`}>
          {s.label}
        </a>
      ))}
    </nav>
  );
}
