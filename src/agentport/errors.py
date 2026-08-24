class AgentPortError(Exception):
    exit_code = 1

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(AgentPortError):
    exit_code = 1


class FormatError(AgentPortError):
    exit_code = 1


class SafetyError(AgentPortError):
    exit_code = 2


class ConflictError(AgentPortError):
    exit_code = 3
