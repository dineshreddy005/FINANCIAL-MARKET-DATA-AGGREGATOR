import { useAuth } from '../context/AuthContext.jsx';
import { useLiveFeed } from '../hooks/useLiveFeed.js';
import Masthead from './Masthead.jsx';
import TickerTape from './TickerTape.jsx';
import QuickNav from './QuickNav.jsx';
import BlotterPanel from './BlotterPanel.jsx';
import LedgerPanel from './LedgerPanel.jsx';
import AnalystPanel from './AnalystPanel.jsx';
import VaultPanel from './VaultPanel.jsx';
import DeskPanel from './DeskPanel.jsx';
import RegistryPanel from './RegistryPanel.jsx';
import AssistantWidget from './AssistantWidget.jsx';

export default function Dashboard() {
  const { auth } = useAuth();
  const feed = useLiveFeed();
  const isAdmin = auth.role === 'admin';

  return (
    <div className="app-shell">
      <Masthead />
      <TickerTape prices={feed.prices} />
      <QuickNav isAdmin={isAdmin} />

      <main className="app-main">
        <BlotterPanel prices={feed.prices} />
        <LedgerPanel prices={feed.prices} />
        <AnalystPanel />
        <VaultPanel />
        <DeskPanel />
        {isAdmin && <RegistryPanel />}
      </main>

      <footer className="app-footer">
        <span className="app-footer__conn">
          <span className={`conn-dot ${feed.connected ? 'conn-dot--on' : 'conn-dot--off'}`} />
          {feed.connected ? 'live feed connected' : 'reconnecting…'}
        </span>
        <span className="mono">{feed.tickCount} ticks received</span>
      </footer>

      <AssistantWidget />
    </div>
  );
}
