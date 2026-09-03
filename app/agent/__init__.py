"""Conversational Rescue Agent — Tanggap60.

Bukan chatbot generik: setiap jawaban state-spesifik WAJIB melewati
tool Tanggap60 (Hermes ``execute_tool`` bila diizinkan state, atau fungsi
service yang sama bila tidak) dan jejaknya dicatat sebagai audit event
AGENT_*. LLM/Hermes hanya memilih tool — tidak pernah menulis kalimat
untuk pengguna. Semua kalimat pengguna berasal dari template tetap di
``app/agent/service.py``.
"""

from app.agent.service import (
    approve_action,
    deny_action,
    handle_message,
)

__all__ = ["approve_action", "deny_action", "handle_message"]
