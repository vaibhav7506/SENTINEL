"""Domain persistence and causal feature hypertable."""

from alembic import op

revision = "0002_features"
down_revision = "0001_timescaledb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hosts (
            id VARCHAR(128) NOT NULL, 
            name VARCHAR(256) NOT NULL, 
            environment VARCHAR(32) NOT NULL, 
            service_job VARCHAR(256), 
            first_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
            last_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE model_versions (
            version VARCHAR(128) NOT NULL, 
            schema_version VARCHAR(80) NOT NULL, 
            schema_hash VARCHAR(64) NOT NULL, 
            artifact_uri VARCHAR(1024) NOT NULL, 
            threshold DOUBLE PRECISION NOT NULL, 
            training_metadata JSONB NOT NULL, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT model_threshold CHECK (threshold BETWEEN 0 AND 1), 
            UNIQUE (version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE chaos_experiments (
            host_id VARCHAR(128) NOT NULL, 
            environment VARCHAR(32) NOT NULL, 
            failure_type VARCHAR(80) NOT NULL, 
            started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
            ended_at TIMESTAMP WITH TIME ZONE, 
            parameters JSONB NOT NULL, 
            expected_degradation JSONB NOT NULL, 
            observed_degradation JSONB, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT chaos_environment CHECK (environment IN ('development','test','demo')), 
            FOREIGN KEY(host_id) REFERENCES hosts (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE evaluation_runs (
            model_version_id UUID, 
            dataset_reference VARCHAR(1024) NOT NULL, 
            split_definition JSONB NOT NULL, 
            metrics JSONB NOT NULL, 
            completed_at TIMESTAMP WITH TIME ZONE, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(model_version_id) REFERENCES model_versions (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE feature_windows (
            host_id VARCHAR(128) NOT NULL, 
            window_end TIMESTAMP WITH TIME ZONE NOT NULL, 
            schema_version VARCHAR(80) NOT NULL, 
            window_start TIMESTAMP WITH TIME ZONE NOT NULL, 
            schema_hash VARCHAR(64) NOT NULL, 
            feature_names JSONB NOT NULL, 
            feature_values JSONB NOT NULL, 
            features JSONB NOT NULL, 
            quality JSONB NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (host_id, window_end, schema_version), 
            CONSTRAINT feature_window_bounds CHECK (window_start < window_end), 
            FOREIGN KEY(host_id) REFERENCES hosts (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_feature_windows_host_time ON feature_windows (host_id, window_end)
        """
    )
    op.execute(
        """
        CREATE TABLE predictions (
            host_id VARCHAR(128) NOT NULL, 
            model_version_id UUID NOT NULL, 
            predicted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
            feature_window_end TIMESTAMP WITH TIME ZONE NOT NULL, 
            schema_version VARCHAR(80) NOT NULL, 
            probability DOUBLE PRECISION NOT NULL, 
            horizon_seconds INTEGER NOT NULL, 
            explanation JSONB NOT NULL, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            CONSTRAINT prediction_probability CHECK (probability BETWEEN 0 AND 1), 
            CONSTRAINT prediction_horizon CHECK (horizon_seconds > 0), 
            FOREIGN KEY(host_id) REFERENCES hosts (id), 
            FOREIGN KEY(model_version_id) REFERENCES model_versions (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE failure_events (
            host_id VARCHAR(128) NOT NULL, 
            experiment_id UUID, 
            failure_type VARCHAR(80) NOT NULL, 
            observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
            confirmed_at TIMESTAMP WITH TIME ZONE, 
            criterion JSONB NOT NULL, 
            evidence JSONB NOT NULL, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(host_id) REFERENCES hosts (id), 
            FOREIGN KEY(experiment_id) REFERENCES chaos_experiments (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE incidents (
            host_id VARCHAR(128) NOT NULL, 
            prediction_id UUID, 
            failure_event_id UUID, 
            status VARCHAR(32) NOT NULL, 
            predicted_at TIMESTAMP WITH TIME ZONE, 
            observed_at TIMESTAMP WITH TIME ZONE, 
            confirmed_at TIMESTAMP WITH TIME ZONE, 
            resolved_at TIMESTAMP WITH TIME ZONE, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(host_id) REFERENCES hosts (id), 
            FOREIGN KEY(prediction_id) REFERENCES predictions (id), 
            FOREIGN KEY(failure_event_id) REFERENCES failure_events (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE alerts (
            incident_id UUID NOT NULL, 
            channel VARCHAR(80) NOT NULL, 
            status VARCHAR(32) NOT NULL, 
            payload JSONB NOT NULL, 
            delivered_at TIMESTAMP WITH TIME ZONE, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(incident_id) REFERENCES incidents (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE remediation_proposals (
            incident_id UUID NOT NULL, 
            runbook_reference VARCHAR(1024) NOT NULL, 
            parameters JSONB NOT NULL, 
            approval_status VARCHAR(32) NOT NULL, 
            execution_status VARCHAR(32) NOT NULL, 
            approved_at TIMESTAMP WITH TIME ZONE, 
            executed_at TIMESTAMP WITH TIME ZONE, 
            id UUID NOT NULL, 
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
            PRIMARY KEY (id), 
            FOREIGN KEY(incident_id) REFERENCES incidents (id)
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable('feature_windows', 'window_end', if_not_exists => TRUE)
        """
    )


def downgrade() -> None:
    op.drop_table("remediation_proposals")
    op.drop_table("alerts")
    op.drop_table("incidents")
    op.drop_table("failure_events")
    op.drop_table("predictions")
    op.drop_table("feature_windows")
    op.drop_table("evaluation_runs")
    op.drop_table("chaos_experiments")
    op.drop_table("model_versions")
    op.drop_table("hosts")
