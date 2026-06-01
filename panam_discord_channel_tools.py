from datetime import timezone


def _get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


async def search_recent_channel_messages(channel, query: str, limit: int = 100) -> str | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    query_lower = query.lower()
    search_limit = min(max(limit, 1), 300)
    matches = []

    async for message in history(limit=search_limit):
        if message.author.bot:
            continue

        content = message.content or ""
        content_lower = content.lower()
        if query_lower not in content_lower:
            continue

        match_index = content_lower.find(query_lower)
        start = max(match_index - 45, 0)
        end = min(match_index + len(query) + 90, len(content))
        excerpt = content[start:end].replace("\n", " ").strip()

        if start > 0:
            excerpt = "…" + excerpt
        if end < len(content):
            excerpt = excerpt + "…"

        created_at = message.created_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        author_name = _get_author_name(message.author)
        matches.append(
            f"{len(matches) + 1}. [{created_at}] {author_name}: {excerpt}"
        )

        if len(matches) >= 10:
            break

    if not matches:
        return ""

    return "Nalezené zprávy:\n" + "\n".join(matches)


async def build_channel_summary_text(channel, limit: int = 50) -> str | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    summary_limit = min(max(limit, 1), 200)
    messages = []

    async for message in history(limit=summary_limit):
        if message.author.bot:
            continue

        content = (message.content or "").strip()
        if not content:
            continue

        created_at = message.created_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        author_name = _get_author_name(message.author)
        messages.append(
            {
                "author": author_name,
                "created_at": created_at,
                "content": content,
            }
        )

    if not messages:
        return ""

    messages.reverse()
    return "\n".join(
        (
            f"Autor: {message['author']}\n"
            f"Čas: {message['created_at']}\n"
            f"Text: {message['content']}"
        )
        for message in messages
    )
