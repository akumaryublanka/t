"""Requester display helpers."""


def requester_name(user) -> str:
    if not user:
        return "Unknown"
    return user.first_name or user.username or str(user.id)
