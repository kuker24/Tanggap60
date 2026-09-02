from __future__ import annotations


class AppError(Exception):
    code = "INTERNAL"
    http_status = 500
    recoverable = False

    def __init__(self, message: str, *, recoverable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        if recoverable is not None:
            self.recoverable = recoverable


class InvalidStateTransition(AppError):
    code = "INVALID_STATE_TRANSITION"
    http_status = 409
    recoverable = False


class InvalidFileType(AppError):
    code = "INVALID_FILE_TYPE"
    http_status = 400
    recoverable = True


class UploadLimitExceeded(AppError):
    code = "UPLOAD_LIMIT_EXCEEDED"
    http_status = 413
    recoverable = True


class EvidenceParseFailed(AppError):
    code = "EVIDENCE_PARSE_FAILED"
    http_status = 422
    recoverable = True


class ManualReviewRequired(AppError):
    code = "MANUAL_REVIEW_REQUIRED"
    http_status = 409
    recoverable = True


class OpenConflicts(AppError):
    code = "OPEN_CONFLICTS"
    http_status = 409
    recoverable = True


class StaleCaseVersion(AppError):
    code = "STALE_CASE_VERSION"
    http_status = 409
    recoverable = True


class ApprovalRequired(AppError):
    code = "APPROVAL_REQUIRED"
    http_status = 409
    recoverable = True


class ApprovalHashMismatch(AppError):
    code = "APPROVAL_HASH_MISMATCH"
    http_status = 409
    recoverable = True


class ArtifactVerifyFailed(AppError):
    code = "ARTIFACT_VERIFY_FAILED"
    http_status = 409
    recoverable = True


class OptionalSignalUnavailable(AppError):
    code = "OPTIONAL_SIGNAL_UNAVAILABLE"
    http_status = 200
    recoverable = True


class ReceiptMismatch(AppError):
    code = "RECEIPT_MISMATCH"
    http_status = 409
    recoverable = True


class CaseExpired(AppError):
    code = "CASE_EXPIRED"
    http_status = 410
    recoverable = False


class ResourceLimit(AppError):
    code = "RESOURCE_LIMIT"
    http_status = 503
    recoverable = True


class IdempotencyConflict(AppError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409
    recoverable = True


class NotFound(AppError):
    code = "NOT_FOUND"
    http_status = 404
    recoverable = False


class Forbidden(AppError):
    code = "FORBIDDEN"
    http_status = 403
    recoverable = False


class ValidationFailed(AppError):
    code = "VALIDATION_FAILED"
    http_status = 400
    recoverable = True
