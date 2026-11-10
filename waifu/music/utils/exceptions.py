"""Music subsystem exception types."""
class MusicError(RuntimeError):
    """Base error raised by music helpers."""


class AudioDownloadError(MusicError):
    """Raised when a source cannot be prepared for playback."""


class VoiceChatError(MusicError):
    """Raised when a voice-chat operation fails."""
