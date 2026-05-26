"""Integration smoke tests for the Composio integration layer.

Each module in this package is an executable script (``python -m
composio_integration.tests.<name>``) that:

* boots Django,
* runs against the real database wrapped in ``transaction.atomic`` blocks
  that are explicitly rolled back so the DB stays clean,
* stubs only the outbound Composio SDK calls (never the DB, never DRF).

Pattern is borrowed from ``wallbit/tests/test_risk_gate_smoke.py``.
"""
