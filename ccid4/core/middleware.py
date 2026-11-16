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
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
