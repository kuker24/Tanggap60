from app.web.labels import human, soften


def test_human_fact() -> None:
    assert human("AMOUNT", "fact") == "Jumlah uang (Rp)"


def test_soften_strips_unit_id_and_pjp() -> None:
    assert "ru_" not in soften("Unit ru_573b132527c7 belum memiliki nominal yang ditinjau.")
    assert "PJP" not in soften("Hubungi bank/PJP untuk unit siap")
    assert soften("Tinjau rekening atau PJP tujuan").startswith("Tinjau rekening")
    assert "mengkirim" not in soften("bukan langsung mengunggah ZIP")
    assert "mengirim" in soften("bukan langsung mengunggah ZIP")
    assert "portal resmi" not in soften("Isi di portal resmi IASC").lower()
