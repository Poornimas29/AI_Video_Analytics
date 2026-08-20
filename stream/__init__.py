# stream/__init__.py
"""Stream package initialization.

Provides easy imports for the primary stream‑related classes used throughout
the project.  The original code referenced a non‑existent ``RTSPStreamReader``
and ``StreamManager`` which caused an ``ImportError`` when the application
started.  Here we alias the existing ``RTSPStream`` implementation to the
expected name and provide a minimal stub for ``StreamManager`` so that the
import succeeds without altering the rest of the codebase.
"""

from .frame_buffer import FrameBuffer
from .rtsp_stream import RTSPStream as RTSPStreamReader  # Alias for compatibility
from .camera_manager import CameraManager
from .grid_renderer import GridRenderer
from .stream_manager import StreamManager
from .reconnect_handler import ReconnectHandler

__all__ = [
    "FrameBuffer",
    "RTSPStreamReader",
    "CameraManager",
    "GridRenderer",
    "StreamManager",
    "ReconnectHandler",
]
