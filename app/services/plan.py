from __future__ import annotations

from app.domain.models import (
    ActionChannel,
    ActionPriority,
    ActionRecord,
    ActionStatus,
    FactRecord,
)
from app.domain.states import Route
from app.services.ids import new_id

FORBIDDEN_PLAN_PHRASES = (
    "dijamin kembali",
    "bekerja sama dengan ojk",
    "bekerja sama dengan iasc",
    "kami mengirim laporan",
)


def build_post_actions(case_id: str, facts: list[FactRecord]) -> list[ActionRecord]:
    actions = [
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NOW,
            channel=ActionChannel.BANK_PJP,
            instruction=(
                "Hubungi bank atau dompet digital lewat nomor resminya. "
                "Sampaikan waktu dan jumlah uang yang sudah Anda cek. "
                "Tanggap60 tidak menghubungi bank."
            ),
            status=ActionStatus.TODO,
            requires_external_user_action=True,
        ),
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NEXT,
            channel=ActionChannel.IASC,
            instruction=(
                "Buka situs resmi IASC. Isi dan kirim laporannya sendiri. "
                "Laporan tidak menjamin uang kembali."
            ),
            status=ActionStatus.TODO,
            official_url_key="IASC",
            requires_external_user_action=True,
        ),
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NEXT,
            channel=ActionChannel.POLICE,
            instruction=(
                "Siapkan cerita kejadian. Hubungi layanan Kepolisian yang resmi dan ikuti petunjuknya."
            ),
            status=ActionStatus.TODO,
            requires_external_user_action=True,
        ),
    ]
    return actions


def build_pre_actions(case_id: str) -> list[ActionRecord]:
    return [
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NOW,
            channel=ActionChannel.MANUAL_VERIFY,
            instruction=(
                "Jangan kirim dana atau data OTP. Verifikasi lewat situs resmi yang Anda kenali sendiri."
            ),
            status=ActionStatus.TODO,
            requires_external_user_action=True,
        )
    ]


def actions_for_route(case_id: str, route: Route, facts: list[FactRecord]) -> list[ActionRecord]:
    if route == Route.PRE_INCIDENT_CHECK:
        return build_pre_actions(case_id)
    return build_post_actions(case_id, facts)
