from epoch_switch import regulation_cli as cli


def test_uc1_active_chain_is_absent_after_fresh_reset():
    """UC1 shelves are empty after the 2026-06-30 profile reset.

    All registry_profiles/ active.json files were deleted so that the next
    CLI run starts fresh with the full R1 → DA1 → R2 → D1 → D2 → CP1 chain.
    """
    active = cli.load_active_set("uc1_ets1_monitoring")

    assert all(record is None for record in active.values())
    assert not cli.active_set_ready(active)


def test_uc2_active_chain_is_absent_until_refreshed():
    """UC2 no longer has tracked migrated active shelves.

    The cleanup leaves UC2 to be rebuilt through the current CLI rather than
    carrying an old pre-D2 migrated profile chain.
    """
    active = cli.load_active_set("uc2_enefg_compliance")

    assert all(record is None for record in active.values())
    assert not cli.active_set_ready(active)
