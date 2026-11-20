-- MealMatch tables (Brief section 8.6). Vectors: Qwen3 Embedding 4B, 2560 dims.
-- No passwords, cookies, session tokens or payment data in any table.

CREATE TABLE IF NOT EXISTS users (
  id VARCHAR(64) PRIMARY KEY,
  address_label VARCHAR(255),
  settings JSON,
  last_distilled_at DATETIME(6)
);

CREATE TABLE IF NOT EXISTS orders (
  id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  run_id VARCHAR(64),
  platform VARCHAR(16) NOT NULL,
  restaurant VARCHAR(255) NOT NULL,
  items JSON,
  cuisine VARCHAR(64),
  price_paid DOUBLE,
  fees DOUBLE,
  eta_min INT,
  rating INT,
  ordered_at DATETIME(6) NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
  id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  run_id VARCHAR(64),
  shown JSON,
  selected VARCHAR(128),
  rejected JSON,
  rejected_restaurants JSON,
  reason TEXT,
  at DATETIME(6) NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
  id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  text TEXT NOT NULL,
  kind VARCHAR(8) NOT NULL,
  source VARCHAR(8) NOT NULL,
  confidence DOUBLE,
  term VARCHAR(64),
  episode_ids JSON,
  vec VECTOR({dim}),
  created_at DATETIME(6)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
  id VARCHAR(128) PRIMARY KEY,
  run_id VARCHAR(64),
  platform VARCHAR(16) NOT NULL,
  restaurant VARCHAR(255) NOT NULL,
  restaurant_id VARCHAR(128),
  address VARCHAR(255),
  item VARCHAR(255) NOT NULL,
  description TEXT,
  price DOUBLE,
  fees DOUBLE,
  eta_min INT,
  currency VARCHAR(8),
  cuisines JSON,
  rating DOUBLE,
  source_url TEXT,
  actor_run_id VARCHAR(64),
  scraped_at DATETIME(6) NOT NULL,
  evidence_id VARCHAR(64),
  dish_vec VECTOR({dim})
);

CREATE TABLE IF NOT EXISTS matches (
  id VARCHAR(64) PRIMARY KEY,
  run_id VARCHAR(64),
  ubereats_offer_id VARCHAR(128),
  doordash_offer_id VARCHAR(128),
  confidence DOUBLE,
  method VARCHAR(8),
  evidence_ids JSON,
  created_at DATETIME(6)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id VARCHAR(64) PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL,
  node VARCHAR(64) NOT NULL,
  at DATETIME(6) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  source_url TEXT,
  actor_run_id VARCHAR(64),
  payload JSON,
  evidence_ids JSON,
  hash CHAR(64)
);
