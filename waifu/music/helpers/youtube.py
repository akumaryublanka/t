"""Cookie-free yt-dlp wrappers used by playback and downloads."""
from waifu.music.downloader import extract_info_sync, extract_playlist_sync, download_track_sync

__all__ = ["extract_info_sync", "extract_playlist_sync", "download_track_sync"]
