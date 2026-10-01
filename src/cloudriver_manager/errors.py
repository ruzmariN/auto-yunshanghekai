class CloudRiverError(RuntimeError):
    """Base application error."""


class SessionExpired(CloudRiverError):
    """The server no longer accepts the saved session."""


class ParallelLearningRejected(CloudRiverError):
    """The platform returned UNTIMED and requires serial execution."""


class LearningDeadlinePassed(CloudRiverError):
    """The platform returned OVERDEADLINE."""


class ActivityBlocked(CloudRiverError):
    """An activity cannot be completed automatically through the resource protocol."""


class ApiError(CloudRiverError):
    def __init__(self, code: str, body: object = None) -> None:
        self.code = code
        self.body = body
        super().__init__(f"API {code}: {body!r}")

