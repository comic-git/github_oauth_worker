"""Client-safe errors shared by future OAuth and enrollment routes."""


class WorkerError(Exception):
    """An expected failure that has an intentionally generic browser-safe response."""

    status_code = 400
    diagnostic_code = "request_failed"
    public_message = "The request could not be completed."

    def safe_log_fields(self) -> dict[str, object]:
        """Return diagnostic metadata that is explicitly safe to send to structured logs."""
        return {}


class WorkerUnavailableError(WorkerError):
    """Report an unavailable worker without revealing deployment configuration."""

    status_code = 503
    diagnostic_code = "worker_unavailable"
    public_message = "The OAuth worker is not configured."
