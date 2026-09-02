from __future__ import annotations

from app.domain.policies import contains_absolute_copy


def test_forbidden_copy_detects_text_not_hash() -> None:
    assert contains_absolute_copy("Paket ini pasti aman untuk dikirim")
    assert contains_absolute_copy("laporan resmi berhasil")
    assert not contains_absolute_copy("2f4ab398c0ffee" * 4)
