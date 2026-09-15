"""mxRaven recipient feedback.

Teaches the spam filter with tenant-supplied raw messages and performs RFC 8058
one-click unsubscribes.
"""

from __future__ import annotations

from mxraven.errors import FeedbackError
from mxraven.feedback.client import AsyncClient, Client, Disposition, LearningResult

__all__ = ["AsyncClient", "Client", "Disposition", "FeedbackError", "LearningResult"]
