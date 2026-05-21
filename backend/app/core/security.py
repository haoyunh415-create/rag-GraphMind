import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def require_api_auth(authorization: str | None = Header(default=None)) -> None:
    """Require a bearer token when API_AUTH_TOKEN is configured."""
    token = get_settings().api_auth_token.strip()
    if not token:
        return

    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied = authorization[len(prefix):].strip()
    if not secrets.compare_digest(supplied, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API authorization token",
        )
