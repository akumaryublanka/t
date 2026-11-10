"""YouTube thumbnail URL helper; the player currently uses text controls."""


def thumbnail_url(video_id: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not video_id or len(video_id) > 64 or any(ch not in allowed for ch in video_id):
        raise ValueError("Invalid YouTube video ID.")
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
