from __future__ import annotations

from app.domain.models import (
    ActionChannel,
    ActionPriority,
    ActionRecord,
    ActionStatus,
    FactRecord,
    FactType,
    ReviewStatus,
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
    reviewed = [f for f in facts if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}]
    has_amount = any(f.type == FactType.AMOUNT for f in reviewed)
    actions = [
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NOW,
            channel=ActionChannel.BANK_PJP,
            instruction=(
                "Hubungi bank atau PJP Anda lewat nomor resmi di aplikasi/kartu/situs resmi. "
                "Sampaikan waktu dan nominal transfer yang sudah Anda konfirmasi. "
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
                "Buka portal IASC resmi yang ditampilkan di layar. Isi data sendiri. "
                "Jangan unggah KTP ke Tanggap60. Pelaporan tidak menjamin dana kembali."
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
                "Siapkan kronologi untuk Laporan Polisi. Pengiriman dilakukan oleh Anda di kanal resmi kepolisian."
            ),
            status=ActionStatus.TODO,
            requires_external_user_action=True,
        ),
    ]
    if has_amount:
        actions.append(
            ActionRecord(
                action_id=new_id("act"),
                case_id=case_id,
                priority=ActionPriority.LATER,
                channel=ActionChannel.ACCOUNT_SECURITY,
                instruction=(
                    "Ganti kata sandi dan hentikan percakapan dengan pihak yang meminta transfer. "
                    "Langkah ini hanya muncul karena ada dana yang tercatat."
                ),
                status=ActionStatus.TODO,
                requires_external_user_action=True,
            )
        )
    return actions


def build_pre_actions(case_id: str) -> list[ActionRecord]:
    return [
        ActionRecord(
            action_id=new_id("act"),
            case_id=case_id,
            priority=ActionPriority.NOW,
            channel=ActionChannel.MANUAL_VERIFY,
            instruction=(
                "Jangan kirim dana atau data OTP. Verifikasi lewat kanal resmi yang Anda kenali sendiri."
            ),
            status=ActionStatus.TODO,
            requires_external_user_action=True,
        )
    ]


def actions_for_route(case_id: str, route: Route, facts: list[FactRecord]) -> list[ActionRecord]:
    if route == Route.PRE_INCIDENT_CHECK:
        return build_pre_actions(case_id)
    return build_post_actions(case_id, facts)
