"""Identity resolution: map any login identity to its canonical account.

Called at JWT-mint time (every login path) and at Telegram bot write time, so a
linked Telegram id transparently acts as its Google account. Unlinked ids resolve
to themselves — standalone accounts keep working unchanged."""
from app.repositories.link import LinkRepository


def resolve_primary(user_id: str, link_repo: LinkRepository = None) -> str:
    return (link_repo or LinkRepository()).get_primary(user_id)
