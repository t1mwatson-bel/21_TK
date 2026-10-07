def apply_win(prediction, dogon_index, bet_amount, cf=None):
    """
    Прогноз выиграл.
    cf — коэффициент на карту (если не передан — берём из coefs.py по карте).
    Списываем все проигранные догоны до win, начисляем выплату,
    сбрасываем current_bet на START_BET.
    """

    if cf is None:
        from coefs import get_dealer_cf
        cf = get_dealer_cf(prediction.get("predicted_card", ""))

    payout = round(bet_amount * cf, 2)

    # Если win был не на Д0 — все предыдущие догоны были проиграны
    total_lost = 0.0
    for i in range(dogon_index):
        total_lost += bet_for_dogon(get_current_bet(), i)

    # Профит = payout - ставка - все проигранные догоны до этого
    profit = round(payout - bet_amount - total_lost, 2)

    bank_state["balance"] = round(
        bank_state["balance"] + payout - bet_amount - total_lost,
        2,
    )
    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "predicted_card": prediction.get("predicted_card"),
        "dogon": dogon_index,
        "bet": bet_amount,
        "cf": cf,
        "payout": payout,
        "profit": profit,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {prediction.get('predicted_card')} × {cf} | "
        f"ставка {bet_amount} ₽ → выплата {payout} ₽ "
        f"(профит {profit} ₽) | баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record
