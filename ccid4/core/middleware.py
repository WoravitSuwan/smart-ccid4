class SecurityHeadersMiddleware:
    """Strict CSP: every script, style and font is served from this server (no CDN)."""

    CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        from django.conf import settings

        agent = getattr(settings, "CARD_AGENT_URL", "")
        csp = (
            self.CSP.replace("connect-src 'self'", f"connect-src 'self' {agent}")
            if agent.startswith("ws://127.0.0.1") or agent.startswith("ws://localhost")
            else self.CSP
        )
        response.setdefault("Content-Security-Policy", csp)
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
