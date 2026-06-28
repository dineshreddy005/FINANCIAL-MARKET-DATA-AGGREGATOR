-- ============================================================================
-- Demo seed data -- gives you working credentials and a sample account out
-- of the box so requirement 4 (masking) and requirement 5 (audit trail) are
-- visibly testable on a fresh `docker compose up` without writing any data
-- by hand. NOT for production: rotate or delete these before going live.
--
-- Passwords (hashed below with PBKDF2-HMAC-SHA256, 260k iterations -- see
-- app/security.py `hash_password` / `verify_password`; stdlib-only, no
-- bcrypt/passlib dependency required):
--   admin       / admin123     role = admin    (sees everything, unmasked)
--   jane.c      / client123    role = client   (sees own account, masked)
--   ingest-svc  / service123   role = service  (machine ingest credential)
-- ============================================================================

INSERT INTO users (username, email, full_name, password_hash, role)
VALUES
    ('admin', 'admin@fmda.local', 'Priya Admin',
     'pbkdf2_sha256$260000$dfc1dfbb978c4b50cee9650a2e287331$aa74ef77b53b279e6c60649daf1f315e38868360bbc6a6bbbb248022a870270a',
     'admin'),
    ('jane.c', 'jane.c@fmda.local', 'Jane Client',
     'pbkdf2_sha256$260000$9cfc79772685c57ef617dc7dc620ce69$d8f348215a9c8fcc6b7a7ab9d7ad07734655a1d1660c34057caf5c06517a48d8',
     'client'),
    ('ingest-svc', 'ingest-svc@fmda.local', 'Ingestion Service Account',
     'pbkdf2_sha256$260000$4f301d43de4ac63cf16eb6ac2c76f151$cf3c29a1b1639719df8215ef238ea81b4b2f8e53353a40b582fc05c8e5dbe38d',
     'service')
ON CONFLICT (username) DO NOTHING;

INSERT INTO client_accounts (user_id, account_number, routing_number, broker_name, account_type, cash_balance)
SELECT id, '4400123456789104', '021000021', 'FMDA Prime Brokerage', 'individual', 18250.50
FROM users WHERE username = 'jane.c'
ON CONFLICT DO NOTHING;

INSERT INTO assets (symbol, asset_type, display_name, currency, sector, market_cap, description, week52_high, week52_low)
VALUES
    ('AAPL', 'equity', 'Apple Inc.', 'USD', 'Technology', 3450000000000,
     'Designs, manufactures, and markets consumer electronics, software, and services.', 240.50, 164.08),
    ('MSFT', 'equity', 'Microsoft Corporation', 'USD', 'Technology', 3180000000000,
     'Develops, licenses, and supports software, services, devices, and solutions worldwide.', 468.35, 309.45),
    ('TSLA', 'equity', 'Tesla, Inc.', 'USD', 'Consumer Cyclical', 920000000000,
     'Designs, develops, manufactures, and sells electric vehicles and energy generation systems.', 488.54, 138.80)
ON CONFLICT (symbol, asset_type) DO UPDATE SET
    sector = EXCLUDED.sector,
    market_cap = EXCLUDED.market_cap,
    description = EXCLUDED.description,
    week52_high = EXCLUDED.week52_high,
    week52_low = EXCLUDED.week52_low,
    profile_updated_at = now();

INSERT INTO account_holdings (account_id, asset_id, quantity, avg_cost)
SELECT ca.id, a.id, 25, 178.30
FROM client_accounts ca, assets a
WHERE ca.user_id = (SELECT id FROM users WHERE username = 'jane.c') AND a.symbol = 'AAPL'
ON CONFLICT DO NOTHING;
