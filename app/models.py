from uuid import uuid4
from sqlalchemy import false
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class SalesOrder(db.Model):
    __tablename__ = "WO Details"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    sales_order = db.Column(db.String(80), nullable=False, index=True)
    release_number = db.Column(db.Integer, nullable=False, default=1)
    is_manual = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    is_shipped = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    shipment_updated_at = db.Column(db.DateTime)
    record_source = db.Column(db.String(20), nullable=False, default='mes', server_default='mes')
    legacy_word_id = db.Column(db.Integer, unique=True)

    @classmethod
    def mes_query(cls):
        return cls.query.filter_by(record_source='mes')
    __table_args__ = (db.UniqueConstraint('sales_order', 'release_number', name='uq_wo_so_release'),)
    customer = db.Column(db.String(200))
    customer_po = db.Column(db.String(120))
    items = db.Column(db.JSON, nullable=False, default=list)
    pushed_at = db.Column(db.DateTime)
    generated_date = db.Column("Generated Date", db.DateTime)
    document_number = db.Column("Document #", db.String(32), unique=True)

    @property
    def part(self):
        return self.items[0].get("item", "") if self.items else ""


class ApprovedSO(db.Model):
    """Approved SO data plus bounded Google review snapshots."""
    __tablename__ = 'approved_work_orders'

    sales_order = db.Column(db.String(80), primary_key=True)
    customer = db.Column(db.String(200))
    customer_po = db.Column(db.String(120))
    items = db.Column(db.JSON, nullable=False, default=list)
    metadata_fields = db.Column(db.JSON, nullable=False, default=dict)
    previous_snapshot = db.Column(db.JSON)
    latest_snapshot = db.Column(db.JSON)
    approved_snapshot = db.Column(db.JSON)
    google_present = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    is_active = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    synced_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    source_revision = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    revision = db.Column(db.Integer, nullable=False, default=0, server_default='0')

    def to_source(self):
        return dict(self.metadata_fields or {}, sales_order=self.sales_order, customer=self.customer,
                    customer_po=self.customer_po, items=self.items, revision=self.revision)

    @property
    def part(self):
        return self.items[0].get('item', '') if self.items else ''


class ProductionOverride(db.Model):
    """Shared with MRP; keyed by SO/QB number, not a MES document number."""
    __tablename__ = 'production_overrides'

    wo_number = db.Column(db.Text, primary_key=True)
    production_date = db.Column(db.Date)
    is_finished_goods = db.Column(db.Boolean, nullable=False, default=False, server_default=false())
    schedule_updated_at = db.Column(db.DateTime)
    schedule_updated_by = db.Column(db.Text)
    finished_goods_updated_at = db.Column(db.DateTime)
    finished_goods_updated_by = db.Column(db.Text)


class PickedQtyOverride(db.Model):
    __tablename__ = 'wo_picked_qty_overrides'

    wo_number = db.Column(db.Text, primary_key=True)
    picked_qty = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
    updated_by = db.Column(db.Text)
