"""In-memory customer registration."""


class EmailAlreadyRegistered(ValueError):
    pass


class UserRegistry:
    def __init__(self) -> None:
        self._emails: set[str] = set()

    def register(self, email: str) -> str:
        """Register an email and return its normalized display value."""
        normalized = email.strip()
        if normalized in self._emails:
            raise EmailAlreadyRegistered(normalized)
        # Seeded defect: uniqueness is case-sensitive.
        self._emails.add(normalized)
        return normalized
