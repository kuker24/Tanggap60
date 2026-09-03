from app.web.labels import human, soften


def test_human_fact() -> None:
    assert human("AMOUNT", "fact") == "Nominal"


def test_soften_strips_unit_id_and_pjp() -> None:
    assert "ru_" not in soften("Unit ru_573b132527c7 belum memiliki nominal yang ditinjau.")
    assert "PJP" not in soften("Hubungi bank/PJP untuk unit siap")
    assert soften("Tinjau rekening atau PJP tujuan").startswith("Tinjau rekening")
