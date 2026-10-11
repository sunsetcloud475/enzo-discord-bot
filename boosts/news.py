"""Client for your own WoW news API.

The response shape isn't known here, so the parser is tolerant: it accepts a
bare list or a dict wrapping the list under common keys, and tries several
common field names. Adjust `normalize()` if your API differs.
"""
import aiohttp
from django.conf import settings

LIST_KEYS = ("results", "articles", "news", "items", "data")


def normalize(item: dict) -> dict | None:
    title = item.get("title") or item.get("headline")
    url = item.get("url") or item.get("link")
    ext_id = item.get("id") or item.get("slug") or url or title
    if not title or not ext_id:
        return None
    return {
        "id": str(ext_id),
        "title": str(title)[:256],
        "url": url,
        "summary": str(
            item.get("summary") or item.get("description") or item.get("excerpt") or ""
        )[:500],
        "image": item.get("image") or item.get("thumbnail") or item.get("image_url"),
    }


async def fetch_news(session: aiohttp.ClientSession) -> list[dict]:
    if not settings.NEWS_API_URL:
        return []
    headers = {}
    if settings.NEWS_API_KEY:
        headers["Authorization"] = f"Bearer {settings.NEWS_API_KEY}"
    async with session.get(
        settings.NEWS_API_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()

    if isinstance(data, dict):
        for key in LIST_KEYS:
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = []
    items = [normalize(i) for i in data if isinstance(i, dict)]
    return [i for i in items if i]
