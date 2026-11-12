from datetime import date

from app.synthetic.generator import (
    DEMO_DATE,
    PROFILES,
    build_all,
    build_fixture,
    final_state,
    minor_to_provider_amount,
    provider_amount_to_minor,
)


def test_sign_convention_round_trips() -> None:
    assert minor_to_provider_amount(-143650) == "1436.50"  # outflow is positive for the provider
    assert minor_to_provider_amount(250000) == "-2500.00"
    assert provider_amount_to_minor("1436.50") == -143650
    assert provider_amount_to_minor("-2500.00") == 250000


def test_fixtures_are_deterministic() -> None:
    assert build_all() == build_all()


def test_every_business_reconciles_opening_plus_activity_to_closing() -> None:
    for profile in PROFILES:
        provider, evaluation = build_fixture(profile)
        state = final_state(provider["pages"])
        for account in provider["accounts"]:
            current = next(
                b
                for b in provider["balances"]
                if b["account_id"] == account["external_account_id"] and b["kind"] == "current"
            )
            as_of = date.fromisoformat(current["as_of"][:10])
            activity = sum(
                provider_amount_to_minor(r["amount"])
                for r in state.values()
                if r["account_id"] == account["external_account_id"]
                and not r["removed"]
                and not r["pending"]
                and date.fromisoformat(r["date"]) <= as_of
            )
            assert account["opening_balance_minor"] + activity == current["amount_minor"], (
                profile.slug,
                account["external_account_id"],
            )
        assert evaluation["expected"]["posted_transaction_count"] > 100


def test_pages_chain_cursors_and_end() -> None:
    for profile in PROFILES:
        provider, _ = build_fixture(profile)
        pages = provider["pages"]
        for previous, page in zip(pages, pages[1:], strict=False):
            assert previous["next_cursor"] == page["cursor"]
            assert previous["has_more"] is True
        assert pages[-1]["has_more"] is False


def test_data_quality_cases_are_present_and_consistent() -> None:
    for profile in PROFILES:
        provider, evaluation = build_fixture(profile)
        cases = evaluation["cases"]
        state = final_state(provider["pages"])
        out_id, in_id = cases["transfer_pair"]
        assert provider_amount_to_minor(state[out_id]["amount"]) == -provider_amount_to_minor(
            state[in_id]["amount"]
        )
        assert state[cases["pending_to_posted"]["pending"]]["removed"] is True
        posted = state[cases["pending_to_posted"]["posted"]]
        assert (
            posted["pending_transaction_id"] == cases["pending_to_posted"]["pending"]
            and posted["pending"] is False
        )
        assert state[cases["removed_by_provider"]]["removed"] is True
        assert state[cases["modified_by_provider"]]["name"].endswith("MERCHANT VERIFIED")
        assert state[cases["remaining_pending"]]["pending"] is True
        occurrences = sum(
            r["transaction_id"] == cases["duplicate_delivery"]
            for page in provider["pages"]
            for r in page["added"]
        )
        assert occurrences == 2
        stale = next(
            b for b in provider["balances"] if b["account_id"] == cases["stale_snapshot_account"]
        )
        assert date.fromisoformat(stale["as_of"][:10]) < DEMO_DATE
        assert provider_amount_to_minor(state[cases["financing_inflow"]]["amount"]) > 0
        assert len(cases["unresolvable"]) >= 2


def test_available_balance_excludes_pending_outflow() -> None:
    for profile in PROFILES:
        provider, _ = build_fixture(profile)
        chk = provider["accounts"][0]["external_account_id"]
        current = next(
            b for b in provider["balances"] if b["account_id"] == chk and b["kind"] == "current"
        )
        available = next(
            b for b in provider["balances"] if b["account_id"] == chk and b["kind"] == "available"
        )
        assert available["amount_minor"] < current["amount_minor"]


def test_evaluation_labels_never_leak_into_provider_payload() -> None:
    for profile in PROFILES:
        provider, evaluation = build_fixture(profile)
        text = str(provider)
        for forbidden in (
            "category",
            "flags",
            "unresolvable",
            "ambiguous",
            "transfer_pair",
            "financing_inflow",
            "pending_to_posted",
            "removed_by_provider",
            "duplicate_delivery",
            "stale_snapshot",
        ):
            assert forbidden not in text, forbidden
        record_keys = {
            "transaction_id",
            "account_id",
            "amount",
            "iso_currency_code",
            "date",
            "name",
            "pending",
            "pending_transaction_id",
        }
        for page in provider["pages"]:
            assert all(set(r) == record_keys for r in page["added"] + page["modified"])
            assert all(r["transaction_id"].startswith("tx-") for r in page["added"])
        assert set(evaluation["labels"]) >= {
            r["transaction_id"] for page in provider["pages"] for r in page["added"]
        }


def test_seed_sql_uses_provider_fixture_only_and_counts_records_once() -> None:
    from app.synthetic.seed import build_sql

    for profile in PROFILES:
        provider, evaluation = build_fixture(profile)
        sql = build_sql(provider, replace=False)
        for forbidden in ("category", "flags", "labels", "expected", "evaluation"):
            assert forbidden not in sql, forbidden
        state = final_state(provider["pages"])
        # Transactions are written by apply_sync_batch, never by the loader itself.
        assert sql.count("insert into public.transactions ") == 0
        assert sql.count("insert into public.transaction_sources ") == 0
        events = sum(
            len(page["added"]) + len(page["modified"]) + len(page["removed"])
            for page in provider["pages"]
        )
        assert sql.count("insert into public.raw_events ") == events
        assert sql.count("insert into public.sync_batches ") == len(provider["pages"])
        assert sql.count("select public.apply_sync_batch(") == len(provider["pages"])
        assert sql.count("::jsonb, null,") == sum(
            len(page["removed"]) for page in provider["pages"]
        )
        assert events > len(
            state
        )  # duplicate delivery, modification and removals are provenance, not extra transactions


def test_september_assumptions_can_create_cash_pressure() -> None:
    """A delayed receipt or an extra expense must be able to squeeze the closing cushion."""
    for profile in PROFILES:
        provider, _ = build_fixture(profile)
        chk = provider["accounts"][0]["external_account_id"]
        closing = next(
            b for b in provider["balances"] if b["account_id"] == chk and b["kind"] == "current"
        )["amount_minor"]
        assert closing > 0
        obligations = sum(
            a["amount_minor"] for a in provider["assumptions"] if a["kind"] != "expected_receipt"
        )
        assert obligations > 0
        # two weeks of obligations should consume a visible share of the cushion
        assert obligations * 3 > closing * 0.25, (profile.slug, closing, obligations)
