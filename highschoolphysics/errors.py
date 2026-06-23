class DomainError(Exception):
    status = 400
    code = "invalid_request"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidRequest(DomainError):
    status = 400
    code = "invalid_request"


class PermissionDenied(DomainError):
    status = 403
    code = "forbidden"


class ResourceNotFound(DomainError):
    status = 404
    code = "not_found"


class StateConflict(DomainError):
    status = 409
    code = "state_conflict"


class PasswordChangeRequired(StateConflict):
    code = "password_change_required"
