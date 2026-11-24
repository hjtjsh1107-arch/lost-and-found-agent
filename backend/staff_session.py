from hmac import compare_digest, digest
from secrets import token_urlsafe
from time import time

from fastapi import HTTPException, Request, Response

COOKIE = "laf_staff_session"
MAX_AGE = 8 * 60 * 60


class StaffSession:
    def __init__(self, key: str):
        self.key = key.encode("utf-8")

    def check_key(self, key: str):
        return compare_digest(key.encode("utf-8"), self.key)

    def sign(self, value):
        return digest(self.key, f"laf-staff-session:{value}".encode(), "sha256").hex()

    def issue(self, request: Request, response: Response):
        payload = f"{int(time()) + MAX_AGE}.{token_urlsafe(24)}"
        response.set_cookie(
            COOKIE, f"{payload}.{self.sign(payload)}", max_age=MAX_AGE, httponly=True,
            secure=request.url.scheme == "https", samesite="strict",
        )

    def active(self, request: Request):
        try:
            expiry, nonce, signature = request.cookies.get(COOKIE, "").split(".")
            return (int(expiry) > time()
                    and compare_digest(signature.encode(), self.sign(f"{expiry}.{nonce}").encode()))
        except ValueError:
            return False

    @staticmethod
    def check_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            raise HTTPException(403, "같은 웹 서비스에서 요청해주세요.")
