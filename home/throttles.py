from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class IpRateThrottle(SimpleRateThrottle):
    """
    Base rate throttle that tracks callers by their client IP address.
    """

    def get_cache_key(self, request, view):
        clientIp = self.get_ident(request)
        if not clientIp:
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": clientIp,
        }


class ClipBurstThrottle(IpRateThrottle):
    """Short-window burst throttle (3/min) for heavy video processing requests."""

    scope = "clip_burst"
    rate = "3/min"  # Allow 3 requests per minute for bursty actions


class ClipHourlyThrottle(IpRateThrottle):
    """Sustained hourly throttle (15/hour) for heavy video processing requests."""

    scope = "clip_hourly"
    rate = "15/hour"  # Allow 15 requests per hour for sustained actions

class SpeedEditBurstThrottle(IpRateThrottle):
    """Short-window burst throttle (3/min) for heavy video processing requests."""

    scope = "speed_edit_burst"
    rate = "5/min"

class SpeedEditHourlyThrottle(IpRateThrottle):
    """Sustained hourly throttle (15/hour) for heavy video processing requests."""

    scope = "speed_edit_hourly"
    rate = "20/hour"


class CreateActionThrottleMixin:
    """Apply `create_throttle_class` to the `create` action of a viewset. leaving other actions unthrottled."""

    create_throttle_classes = ()

    def get_throttles(self):
        # Allow local development to bypass throttling via ENABLE_RATE_LIMIT=False in .env
        if not getattr(settings, "ENABLE_RATE_LIMIT", True):
            return []

        if getattr(self, "action", None) == "create":
            return [throttleClass() for throttleClass in self.create_throttle_classes]
        return super().get_throttles()