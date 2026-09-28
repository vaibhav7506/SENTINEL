"""Preserve existing demo data and add account ownership, enrollment and real RLS."""

from alembic import op

revision = "0007_saas_foundation"
down_revision = "0006_governed_proposals"
branch_labels = None
depends_on = None

# ruff: noqa: E501

UPGRADE_SQL = (
    """CREATE TABLE accounts (
	name VARCHAR(160) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
)""",
    """INSERT INTO accounts(id,name) VALUES ('00000000-0000-4000-8000-000000000001','Sentinel development/demo account')""",
    """ALTER TABLE hosts ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_hosts_account_id ON hosts(account_id)""",
    """ALTER TABLE feature_windows ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_feature_windows_account_id ON feature_windows(account_id)""",
    """ALTER TABLE predictions ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_predictions_account_id ON predictions(account_id)""",
    """ALTER TABLE incidents ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_incidents_account_id ON incidents(account_id)""",
    """ALTER TABLE alerts ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_alerts_account_id ON alerts(account_id)""",
    """ALTER TABLE chaos_experiments ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_chaos_experiments_account_id ON chaos_experiments(account_id)""",
    """ALTER TABLE failure_events ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_failure_events_account_id ON failure_events(account_id)""",
    """ALTER TABLE service_observations ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_service_observations_account_id ON service_observations(account_id)""",
    """ALTER TABLE remediation_proposals ADD COLUMN account_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid REFERENCES accounts(id)""",
    """CREATE INDEX ix_remediation_proposals_account_id ON remediation_proposals(account_id)""",
    """ALTER TABLE hosts ADD COLUMN display_name varchar(256) NOT NULL DEFAULT ''""",
    """ALTER TABLE hosts ADD COLUMN hostname varchar(256) NOT NULL DEFAULT ''""",
    """ALTER TABLE hosts ADD COLUMN agent_version varchar(64) NOT NULL DEFAULT ''""",
    """ALTER TABLE hosts ADD COLUMN platform varchar(64) NOT NULL DEFAULT ''""",
    """ALTER TABLE hosts ADD COLUMN architecture varchar(64) NOT NULL DEFAULT ''""",
    """ALTER TABLE hosts ADD COLUMN status varchar(32) NOT NULL DEFAULT 'active'""",
    """ALTER TABLE hosts ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()""",
    """UPDATE hosts SET display_name=name,hostname=name,created_at=first_seen""",
    """CREATE TABLE users (
	email VARCHAR(320) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	role VARCHAR(12) NOT NULL, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT user_role CHECK (role IN ('OWNER','ADMIN','MEMBER')), 
	UNIQUE (email), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX ix_users_account_id ON users (account_id)""",
    """CREATE TABLE auth_sessions (
	account_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	UNIQUE (token_hash)
)""",
    """CREATE TABLE enrollment_tokens (
	token_hash VARCHAR(64) NOT NULL, 
	prefix VARCHAR(24) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX ix_enrollment_tokens_account_id ON enrollment_tokens (account_id)""",
    """CREATE TABLE agent_credentials (
	host_id VARCHAR(128) NOT NULL, 
	key_prefix VARCHAR(32) NOT NULL, 
	key_hash VARCHAR(64) NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(host_id) REFERENCES hosts (id), 
	UNIQUE (key_hash), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX ix_agent_credentials_account_id ON agent_credentials (account_id)""",
    """CREATE TABLE alert_channels (
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	destination VARCHAR(2048) NOT NULL, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX ix_alert_channels_account_id ON alert_channels (account_id)""",
    """CREATE TABLE audit_events (
	actor_user_id UUID, 
	action VARCHAR(80) NOT NULL, 
	resource_id VARCHAR(128), 
	details JSONB DEFAULT '{}' NOT NULL, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX ix_audit_events_account_id ON audit_events (account_id)""",
    """CREATE TABLE metrics (
	host_id VARCHAR(128) NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	values JSONB NOT NULL, 
	account_id UUID DEFAULT '00000000-0000-4000-8000-000000000001'::uuid NOT NULL, 
	PRIMARY KEY (host_id, observed_at), 
	FOREIGN KEY(host_id) REFERENCES hosts (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)""",
    """CREATE INDEX metrics_observed_at_idx ON metrics (observed_at DESC)""",
    """CREATE INDEX ix_metrics_host_time ON metrics (host_id, observed_at)""",
    """CREATE INDEX ix_metrics_account_id ON metrics (account_id)""",
    """SELECT create_hypertable('metrics','observed_at',if_not_exists=>TRUE)""",
    """DO $$ BEGIN
IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='sentinel_app') THEN
CREATE ROLE sentinel_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
END IF;
IF EXISTS(SELECT FROM pg_roles WHERE rolname='sentinel_app' AND (rolsuper OR rolbypassrls)) THEN
RAISE EXCEPTION 'sentinel_app must not bypass row security'; END IF;
EXECUTE format('GRANT sentinel_app TO %I',current_user);
END $$""",
    """GRANT USAGE ON SCHEMA public TO sentinel_app""",
    """GRANT SELECT ON model_versions,evaluation_runs TO sentinel_app""",
    """ALTER TABLE hosts ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON hosts USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON hosts TO sentinel_app""",
    """ALTER TABLE feature_windows ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON feature_windows USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON feature_windows TO sentinel_app""",
    """ALTER TABLE predictions ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON predictions USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON predictions TO sentinel_app""",
    """ALTER TABLE incidents ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON incidents USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON incidents TO sentinel_app""",
    """ALTER TABLE alerts ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON alerts USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON alerts TO sentinel_app""",
    """ALTER TABLE chaos_experiments ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON chaos_experiments USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON chaos_experiments TO sentinel_app""",
    """ALTER TABLE failure_events ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON failure_events USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON failure_events TO sentinel_app""",
    """ALTER TABLE service_observations ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON service_observations USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON service_observations TO sentinel_app""",
    """ALTER TABLE remediation_proposals ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON remediation_proposals USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON remediation_proposals TO sentinel_app""",
    """ALTER TABLE users ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON users USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON users TO sentinel_app""",
    """ALTER TABLE enrollment_tokens ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON enrollment_tokens USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON enrollment_tokens TO sentinel_app""",
    """ALTER TABLE agent_credentials ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON agent_credentials USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON agent_credentials TO sentinel_app""",
    """ALTER TABLE alert_channels ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON alert_channels USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON alert_channels TO sentinel_app""",
    """ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON audit_events USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT ON audit_events TO sentinel_app""",
    """ALTER TABLE metrics ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON metrics USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON metrics TO sentinel_app""",
    """ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON auth_sessions USING(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(account_id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT, INSERT, UPDATE ON auth_sessions TO sentinel_app""",
    """ALTER TABLE accounts ENABLE ROW LEVEL SECURITY""",
    """CREATE POLICY tenant_isolation ON accounts USING(id=nullif(current_setting('sentinel.account_id',true),'')::uuid) WITH CHECK(id=nullif(current_setting('sentinel.account_id',true),'')::uuid)""",
    """GRANT SELECT,UPDATE ON accounts TO sentinel_app""",
    """CREATE FUNCTION sentinel_ownership_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE owner_account uuid; related_account uuid;
BEGIN
 IF TG_TABLE_NAME='hosts' THEN
  IF TG_OP='UPDATE' AND NEW.account_id IS DISTINCT FROM OLD.account_id THEN RAISE EXCEPTION 'Host ownership is immutable'; END IF;
  RETURN NEW;
 ELSIF TG_TABLE_NAME IN ('alerts','remediation_proposals') THEN
  SELECT account_id INTO owner_account FROM public.incidents WHERE id=NEW.incident_id;
 ELSE
  SELECT account_id INTO owner_account FROM public.hosts WHERE id=NEW.host_id;
 END IF;
 IF owner_account IS NULL THEN RAISE EXCEPTION 'Owning resource is unavailable'; END IF;
 NEW.account_id=owner_account;
 IF TG_TABLE_NAME IN ('failure_events','service_observations') THEN
 IF NEW.experiment_id IS NOT NULL THEN
  SELECT account_id INTO related_account FROM public.chaos_experiments WHERE id=NEW.experiment_id;
  IF related_account IS DISTINCT FROM owner_account THEN RAISE EXCEPTION 'Experiment ownership mismatch'; END IF;
 END IF; END IF;
 IF TG_TABLE_NAME IN ('incidents','remediation_proposals') THEN
 IF NEW.prediction_id IS NOT NULL THEN
  SELECT account_id INTO related_account FROM public.predictions WHERE id=NEW.prediction_id;
  IF related_account IS DISTINCT FROM owner_account THEN RAISE EXCEPTION 'Prediction ownership mismatch'; END IF;
 END IF; END IF;
 IF TG_TABLE_NAME='incidents' THEN
 IF NEW.failure_event_id IS NOT NULL THEN
  SELECT account_id INTO related_account FROM public.failure_events WHERE id=NEW.failure_event_id;
  IF related_account IS DISTINCT FROM owner_account THEN RAISE EXCEPTION 'Failure ownership mismatch'; END IF;
 END IF; END IF;
 RETURN NEW;
END $$""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON hosts FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON feature_windows FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON predictions FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON incidents FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON alerts FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON chaos_experiments FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON failure_events FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON service_observations FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON remediation_proposals FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON metrics FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE TRIGGER ownership_guard BEFORE INSERT OR UPDATE ON agent_credentials FOR EACH ROW EXECUTE FUNCTION sentinel_ownership_guard()""",
    """CREATE FUNCTION sentinel_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit events are append-only'; END $$""",
    """CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION sentinel_audit_immutable()""",
)

DOWNGRADE_SQL = (
    """DROP POLICY tenant_isolation ON hosts""",
    """ALTER TABLE hosts DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON feature_windows""",
    """ALTER TABLE feature_windows DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON predictions""",
    """ALTER TABLE predictions DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON incidents""",
    """ALTER TABLE incidents DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON alerts""",
    """ALTER TABLE alerts DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON chaos_experiments""",
    """ALTER TABLE chaos_experiments DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON failure_events""",
    """ALTER TABLE failure_events DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON service_observations""",
    """ALTER TABLE service_observations DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON remediation_proposals""",
    """ALTER TABLE remediation_proposals DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON users""",
    """ALTER TABLE users DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON enrollment_tokens""",
    """ALTER TABLE enrollment_tokens DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON agent_credentials""",
    """ALTER TABLE agent_credentials DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON alert_channels""",
    """ALTER TABLE alert_channels DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON audit_events""",
    """ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON metrics""",
    """ALTER TABLE metrics DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON auth_sessions""",
    """ALTER TABLE auth_sessions DISABLE ROW LEVEL SECURITY""",
    """DROP POLICY tenant_isolation ON accounts""",
    """ALTER TABLE accounts DISABLE ROW LEVEL SECURITY""",
    """DROP TRIGGER ownership_guard ON hosts""",
    """DROP TRIGGER ownership_guard ON feature_windows""",
    """DROP TRIGGER ownership_guard ON predictions""",
    """DROP TRIGGER ownership_guard ON incidents""",
    """DROP TRIGGER ownership_guard ON alerts""",
    """DROP TRIGGER ownership_guard ON chaos_experiments""",
    """DROP TRIGGER ownership_guard ON failure_events""",
    """DROP TRIGGER ownership_guard ON service_observations""",
    """DROP TRIGGER ownership_guard ON remediation_proposals""",
    """DROP TRIGGER ownership_guard ON metrics""",
    """DROP TRIGGER ownership_guard ON agent_credentials""",
    """DROP FUNCTION sentinel_ownership_guard()""",
    """DROP TABLE metrics""",
    """DROP TABLE audit_events""",
    """DROP TABLE alert_channels""",
    """DROP TABLE agent_credentials""",
    """DROP TABLE enrollment_tokens""",
    """DROP TABLE auth_sessions""",
    """DROP TABLE users""",
    """DROP FUNCTION sentinel_audit_immutable()""",
    """ALTER TABLE hosts DROP COLUMN account_id""",
    """ALTER TABLE feature_windows DROP COLUMN account_id""",
    """ALTER TABLE predictions DROP COLUMN account_id""",
    """ALTER TABLE incidents DROP COLUMN account_id""",
    """ALTER TABLE alerts DROP COLUMN account_id""",
    """ALTER TABLE chaos_experiments DROP COLUMN account_id""",
    """ALTER TABLE failure_events DROP COLUMN account_id""",
    """ALTER TABLE service_observations DROP COLUMN account_id""",
    """ALTER TABLE remediation_proposals DROP COLUMN account_id""",
    """ALTER TABLE hosts DROP COLUMN display_name""",
    """ALTER TABLE hosts DROP COLUMN hostname""",
    """ALTER TABLE hosts DROP COLUMN agent_version""",
    """ALTER TABLE hosts DROP COLUMN platform""",
    """ALTER TABLE hosts DROP COLUMN architecture""",
    """ALTER TABLE hosts DROP COLUMN status""",
    """ALTER TABLE hosts DROP COLUMN created_at""",
    """DROP TABLE accounts""",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
