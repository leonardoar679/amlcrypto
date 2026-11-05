"""The statements the platform sends to MySQL, checked without a MySQL server.

PyMySQL runs `executemany` of an INSERT as ONE multi-row statement: it takes the VALUES template,
repeats it for each row, and appends whatever follows (here: ON DUPLICATE KEY UPDATE ...) exactly
as written. So nothing after VALUES may take a bound parameter, or every row's parameters would be
counted against a placeholder that is not repeated, and the statement fails. SQLite does not care,
so this is the kind of mistake only the MySQL CI jobs would otherwise find.
"""

from __future__ import annotations

from dataclasses import replace

import pymysql.cursors as pymysql_cursors
from sqlalchemy.dialects import mysql

import app.database.upsert as upsert_module
from app.chains.solana.models import Unattributed
from app.database.repositories.solana import save_solana_transactions
from app.ingestion.raw import RawCollector
from tests import test_solana_store as solana_store
from tests import test_sync_account as account
from tests import test_sync_solana as solana
from tests import test_sync_utxo as bitcoin
from tests.rigs import Rig


def test_every_bulk_upsert_the_platform_builds_can_be_batched_by_pymysql(
    committing_db, monkeypatch
):
    built: list[str] = []
    real = upsert_module.upsert_statement

    def recording(dialect, model, **options):
        # Whatever database this test runs on, also build the statement MySQL would be sent.
        built.append(str(real("mysql", model, **options).compile(dialect=mysql.dialect())))
        return real(dialect, model, **options)

    monkeypatch.setattr(upsert_module, "upsert_statement", recording)

    # Every table written in bulk: an account chain, Bitcoin and Solana, with raw answers kept.
    account.sync(account.seeded(), committing_db, account.Clock())
    rig = Rig()
    bitcoin.flow(rig)
    bitcoin.sync(rig, committing_db, bitcoin.Clock(), raw=RawCollector())
    rig = Rig()
    solana.flow(rig)
    solana.sync(rig, committing_db, solana.Clock(), raw=RawCollector())

    # Balance changes that no instruction explains are saved only when a transaction has some.
    odd = replace(
        solana_store.decode("system_transfer_v0"),
        unattributed=(Unattributed("sol", "W" * 44, None, None, "-250"),),
    )
    with committing_db.write() as session:
        save_solana_transactions(session, [odd], address="W" * 44)
        session.commit()

    tables = set()
    for sql in built:
        batched = pymysql_cursors.RE_INSERT_VALUES.match(sql)
        assert batched, f"PyMySQL would not batch this statement: {sql}"
        assert "%s" not in (batched.group(3) or ""), f"a parameter after VALUES: {sql}"
        tables.add(sql.split()[2])
    assert tables == {
        "transactions",
        "transfers",
        "transaction_inputs",
        "transaction_outputs",
        "balance_changes",
        "unattributed_changes",
        "address_transactions",
        "data_coverage",
        "raw_responses",
    }
