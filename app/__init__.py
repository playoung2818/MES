import os
from flask import Flask
from dotenv import load_dotenv
from sqlalchemy import inspect, text
from .models import db

def create_app():
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_DSN")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    with app.app_context():
        from .approved_so_schema import rename_approved_so_table, ensure_approved_so_schema
        rename_approved_so_table()
        db.create_all()
        ensure_approved_so_schema()
        _ensure_schema()
        from .schema import migrate_multiple_wos
        migrate_multiple_wos()
        with db.engine.begin() as connection:
            columns = {c['name'] for c in inspect(connection).get_columns('WO Details')}
            if 'is_manual' not in columns:
                connection.execute(text('ALTER TABLE "WO Details" ADD COLUMN is_manual BOOLEAN NOT NULL DEFAULT FALSE'))

    return app


def _ensure_schema():
    """Small additive migration for existing installs."""
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("WO Details")}
    with db.engine.begin() as connection:
        if "pushed_at" not in columns:
            connection.execute(text('ALTER TABLE "WO Details" ADD COLUMN pushed_at TIMESTAMP'))
        if "Generated Date" not in columns:
            connection.execute(text('ALTER TABLE "WO Details" ADD COLUMN "Generated Date" TIMESTAMP'))
        if "Document #" not in columns:
            connection.execute(text('ALTER TABLE "WO Details" ADD COLUMN "Document #" VARCHAR(32)'))
        connection.execute(text('''CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_document_number
                                   ON "WO Details" ("Document #")'''))
        # The original creation time was not previously recorded. Use the available
        # saved timestamp for legacy records; leave unknown dates NULL.
        if "Generated Date" not in columns:
            connection.execute(text('''UPDATE "WO Details" SET "Generated Date" = pushed_at'''))
        # One-time legacy seed: never assign it to imported history or multiple releases.
        source_filter = " AND record_source = 'mes'" if 'record_source' in columns else ''
        connection.execute(text('''UPDATE "WO Details" SET "Document #" = 'WO-2609-0127'
            WHERE sales_order = 'SO-20261207' AND "Document #" IS NULL''' + source_filter + '''
            AND NOT EXISTS (SELECT 1 FROM "WO Details" WHERE "Document #" = 'WO-2609-0127')
            AND (SELECT COUNT(*) FROM "WO Details"
                 WHERE sales_order = 'SO-20261207' AND "Document #" IS NULL''' + source_filter + ') = 1'))
