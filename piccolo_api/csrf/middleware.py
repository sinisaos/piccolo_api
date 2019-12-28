from collections.abc import Sequence
import uuid
import typing as t

from starlette.datastructures import URL
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
    Request,
)
from starlette.types import ASGIApp
from starlette.exceptions import HTTPException


SAFE_HTTP_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")
ONE_YEAR = 31536000  # 365 * 24 * 60 * 60
DEFAULT_COOKIE_NAME = "csrftoken"
DEFAULT_HEADER_NAME = "X-CSRFToken"


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    For GET requests, set a random token as a cookie. For unsafe HTTP methods,
    require a HTTP header to match the cookie value, otherwise the request
    is rejected.

    This uses the Double Submit Cookie style of CSRF prevention. For more
    information:

    https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#double-submit-cookie
    https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#use-of-custom-request-headers

    This is currently only intended for use using AJAX - since the CSRF token
    needs to be added to the request header.
    """

    @staticmethod
    def get_new_token() -> str:
        return str(uuid.uuid4())

    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: t.Sequence[str] = [],
        cookie_name=DEFAULT_COOKIE_NAME,
        header_name=DEFAULT_HEADER_NAME,
        max_age=ONE_YEAR,
        **kwargs,
    ):
        if not isinstance(allowed_hosts, Sequence):
            raise ValueError(
                "allowed_hosts must be a sequence (list or tuple)"
            )

        self.allowed_hosts = allowed_hosts
        self.cookie_name = cookie_name
        self.header_name = header_name
        self.max_age = max_age
        super().__init__(app, **kwargs)

    def is_valid_referer(self, request: Request) -> bool:
        header: str = (
            request.headers.get("origin")
            or request.headers.get("referer")
            or ""
        )

        url = URL(header)
        hostname = url.hostname
        is_valid = hostname in self.allowed_hosts if hostname else False
        return is_valid

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ):
        if request.method in SAFE_HTTP_METHODS:
            response = await call_next(request)
            if not request.cookies.get(self.cookie_name):
                response.set_cookie(
                    self.cookie_name,
                    self.get_new_token(),
                    max_age=self.max_age,
                )
            return response
        else:
            cookie_token = request.cookies.get(self.cookie_name)
            if not cookie_token:
                raise HTTPException(403, "No CSRF cookie found")

            header_token = request.headers.get(self.header_name)

            if cookie_token != header_token:
                raise HTTPException(403, "CSRF tokens don't match")

            # Provides defence in depth:
            if request.base_url.is_secure:
                # According to this paper, the referer header is present in
                # the vast majority of HTTPS requests, but not HTTP requests,
                # so only check it for HTTPS.
                # https://seclab.stanford.edu/websec/csrf/csrf.pdf
                if not self.is_valid_referer(request):
                    raise HTTPException(403, "Referer or origin is incorrect")

            return await call_next(request)