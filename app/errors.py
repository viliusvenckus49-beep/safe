"""Business failures carry stable codes, never user-facing text."""


class DomainError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
