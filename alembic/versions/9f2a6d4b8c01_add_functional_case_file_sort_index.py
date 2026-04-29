"""add functional case file sort index

Revision ID: 9f2a6d4b8c01
Revises: 5ac10ab42c29
Create Date: 2026-04-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9f2a6d4b8c01'
down_revision = '5ac10ab42c29'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'pity_functional_case_file',
        sa.Column('sort_index', sa.Integer(), nullable=False, server_default='0', comment='排序'),
    )
    op.alter_column('pity_functional_case_file', 'sort_index', server_default=None)


def downgrade():
    op.drop_column('pity_functional_case_file', 'sort_index')
