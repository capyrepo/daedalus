import pam
from fastapi import Request
from fastapi.responses import RedirectResponse


def verify_system_credentials(username: str, password: str) -> bool:
    p = pam.pam()
    return p.authenticate(username, password, service="login")


def require_auth(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=302)
