"""
sso.py  (API router)
====================
WHAT THIS FILE DOES
-------------------
"Continue with SSO": signing in through an OpenID Connect (OIDC) provider --
Google, Microsoft Entra ID, Okta, Auth0, Keycloak -- instead of a password.

    GET  /api/v1/auth/sso/config     is SSO on? what should the button say?
    GET  /api/v1/auth/sso/login      redirect the browser to the provider
    GET  /api/v1/auth/sso/callback   the provider sends the browser back here
    POST /api/v1/auth/sso/exchange   the frontend swaps a one-time code for
                                     the usual access + refresh tokens

Off unless OIDC_ENABLED and the discovery URL, client id and client secret
are all set (settings.oidc_configured). While off, /config says so and every
other route answers 404.

THE FLOW, STEP BY STEP (the "authorization code flow with PKCE")
----------------------------------------------------------------
  1. The login page links to /sso/login. We create:
        state     a short-lived token WE signed, carrying a nonce, the PKCE
                  verifier and (optionally) an invite token. It proves the
                  callback answers a login WE started.
        a cookie  holding a random "binding" value that is also inside the
                  state. An attacker who starts a login and tricks YOUR
                  browser into finishing it (login CSRF) fails: your browser
                  does not have their cookie.
     ...and redirect the browser to the provider's login page.

  2. The user signs in AT THE PROVIDER. We never see their password.

  3. The provider redirects back to /sso/callback with a one-time `code`.
     Server to server, we exchange it (plus our client secret and the PKCE
     verifier) for an ID TOKEN: a JWT the provider signed, saying who the
     user is. We check its signature against the provider's published keys
     (JWKS), its issuer, its audience (our client id), its expiry and our
     nonce. Only then do we believe the email address inside it.

  4. We find or create the DocMinds account (rules below), then redirect
     the browser to the FRONTEND with a one-time, two-minute code in the URL
     FRAGMENT (#code=...). A fragment is never sent to any server, so it does
     not end up in access logs or Referer headers.

  5. The frontend POSTs that code to /sso/exchange and receives exactly what
     a password login returns. The code is single-use (stored hashed, like a
     password-reset token), so a copy of the URL is worthless afterwards.

WHO GETS IN -- ACCOUNT LINKING AND TENANCY
------------------------------------------
Accounts are matched by EMAIL, and only an email the provider says it has
VERIFIED is trusted (OIDC_REQUIRE_EMAIL_VERIFIED). Without that check,
anyone able to set an unverified email at some provider could sign in as
the DocMinds user who owns that address.

    an account with that email exists -> sign in to it ("linking"); the
                                         address is now proven, so the
                                         account is marked verified
    no account, invite in the link    -> join the inviting organization with
                                         the role the Admin chose -- only if
                                         the invite was sent to THIS email
    no account, no invite             -> create a NEW organization, with the
                                         user as its Admin -- only when
                                         OIDC_ALLOW_SIGNUP is on

There is deliberately NO "join the organization whose domain matches my
email" rule: joining an existing organization always takes an invite,
exactly as with password signup (see api/v1/auth.py).
"""

import base64
import datetime
import hashlib
import logging
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import jwt  # PyJWT
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.auth import find_usable_invite
from app.core import roles, security
from app.core.config import settings
from app.models.organization import Organization
from app.models.user import User
from app.schemas.token import Token
from app.services import audit, sessions

logger = logging.getLogger(__name__)

router = APIRouter()

# The UserToken purpose for the one-time code handed to the frontend.
SSO_HANDOFF = "sso_handoff"
HANDOFF_LIFETIME = datetime.timedelta(minutes=2)
# How long the user may spend at the provider's login page.
STATE_LIFETIME_SECONDS = 10 * 60
STATE_COOKIE = "docminds_sso"

# Algorithms we accept on an ID token. Asymmetric only: "HS256" would mean
# the token is signed with OUR client secret, and "none" means unsigned.
_ID_TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]

# Tests swap in an httpx.MockTransport here; None means the real network.
_transport = None


# ======================================================================
# PROVIDER METADATA (discovery document + signing keys), cached
# ======================================================================
# Both are fetched over the network and change rarely, so they are kept for
# an hour. A login that sees an unknown key id refreshes the keys once --
# that is how a provider's routine key rotation is picked up.
_CACHE_SECONDS = 3600
_metadata_cache: Dict[str, Any] = {}


def _http():
    import httpx

    return httpx.AsyncClient(timeout=10, transport=_transport, follow_redirects=False)


async def _discovery() -> Dict[str, Any]:
    cached = _metadata_cache.get("discovery")
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async with _http() as client:
        response = await client.get(settings.OIDC_DISCOVERY_URL)
    response.raise_for_status()
    document = response.json()
    for field in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(field):
            raise RuntimeError(f"The OIDC discovery document has no '{field}'.")
    _metadata_cache["discovery"] = (time.monotonic() + _CACHE_SECONDS, document)
    return document


async def _jwks(force: bool = False) -> Dict[str, Any]:
    cached = _metadata_cache.get("jwks")
    if not force and cached and cached[0] > time.monotonic():
        return cached[1]
    discovery = await _discovery()
    async with _http() as client:
        response = await client.get(discovery["jwks_uri"])
    response.raise_for_status()
    keys = response.json()
    _metadata_cache["jwks"] = (time.monotonic() + _CACHE_SECONDS, keys)
    return keys


def clear_metadata_cache() -> None:
    """Forget the provider's metadata (tests, or after changing provider)."""
    _metadata_cache.clear()


# ======================================================================
# HELPERS
# ======================================================================


def _require_sso() -> None:
    if not settings.oidc_configured:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _redirect_uri(request: Request) -> str:
    if settings.OIDC_REDIRECT_URI:
        return settings.OIDC_REDIRECT_URI
    return str(request.url_for("sso_callback"))


def _pkce_challenge(verifier: str) -> str:
    """PKCE S256: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _frontend_redirect(**fragment: str) -> RedirectResponse:
    """Back to the frontend's /login/sso page, results in the URL fragment."""
    response = RedirectResponse(
        f"{settings.frontend_url}/login/sso#{urlencode(fragment)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(STATE_COOKIE, path=f"{settings.API_V1_STR}/auth/sso")
    return response


class _SSOError(Exception):
    """A failure whose message is safe to show the user."""


def _email_is_verified(claims: Dict[str, Any]) -> bool:
    if not settings.OIDC_REQUIRE_EMAIL_VERIFIED:
        return True
    value = claims.get("email_verified")
    # Some providers send the JSON string "true" instead of a boolean.
    return value is True or (isinstance(value, str) and value.lower() == "true")


async def _verify_id_token(id_token: str, nonce: str) -> Dict[str, Any]:
    discovery = await _discovery()
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError:
        raise _SSOError("The identity provider returned a malformed ID token.") from None
    if header.get("alg") not in _ID_TOKEN_ALGORITHMS:
        raise _SSOError("The ID token is not signed with an accepted algorithm.")

    key = None
    for attempt in range(2):
        keys = await _jwks(force=attempt == 1)
        for candidate in keys.get("keys", []):
            if header.get("kid") in (None, candidate.get("kid")):
                key = jwt.PyJWK(candidate)
                break
        if key is not None:
            break
    if key is None:
        raise _SSOError("The ID token was signed with an unknown key.")

    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=_ID_TOKEN_ALGORITHMS,
            audience=settings.OIDC_CLIENT_ID,
            issuer=discovery["issuer"],
            leeway=60,  # tolerate a minute of clock difference
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise _SSOError(f"The ID token could not be verified ({type(exc).__name__}).") from None

    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise _SSOError("The ID token does not belong to this sign-in attempt.")
    return claims


async def _exchange_code(code: str, verifier: str, redirect_uri: str) -> Dict[str, Any]:
    discovery = await _discovery()
    async with _http() as client:
        response = await client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.OIDC_CLIENT_ID,
                "client_secret": settings.OIDC_CLIENT_SECRET,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        logger.warning("OIDC code exchange failed", extra={"status_code": response.status_code})
        raise _SSOError("The identity provider rejected the sign-in. Please try again.")
    tokens = response.json()
    if not tokens.get("id_token"):
        raise _SSOError("The identity provider did not return an ID token.")
    return tokens


async def _find_or_create_user(
    db: AsyncSession, request: Request, email: str, invite_token: Optional[str]
) -> User:
    """The account-linking and tenancy rules from the module docstring."""
    result = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    user = result.scalar_one_or_none()

    if user is not None:
        if not user.is_active:
            raise _SSOError("This account has been deactivated.")
        # The provider has just proven the address belongs to this person.
        user.is_verified = True
        return user

    # A random password nobody knows: the account signs in through SSO, or
    # sets a real password later with "Forgot password?".
    unusable_password = security.get_password_hash(security.generate_token())

    if invite_token:
        invite = await find_usable_invite(db, invite_token)
        if invite is None:
            raise _SSOError("This invite link is invalid, has expired, or has already been used.")
        if invite.email.lower() != email.lower():
            raise _SSOError("This invite was sent to a different email address.")
        user = User(
            email=email, hashed_password=unusable_password, role=invite.role,
            org_id=invite.org_id, is_active=True, is_verified=True,
        )
        db.add(user)
        invite.accepted_at = sessions.utcnow()
        await db.flush()
        await audit.record(
            db, "user.signup", org_id=user.org_id, user_id=user.id, request=request,
            details={"email": email, "role": user.role, "via_invite": True, "method": "sso"},
        )
        await audit.record(
            db, "invite.accept", org_id=user.org_id, user_id=user.id, request=request,
            details={"invite_id": invite.id, "email": invite.email, "role": invite.role},
        )
        return user

    if not settings.OIDC_ALLOW_SIGNUP:
        raise _SSOError(
            "There is no DocMinds account for this email. Ask an Admin of your "
            "organization to invite you."
        )

    organization = Organization(name=f"{email.split('@')[0]}'s organization")
    db.add(organization)
    await db.flush()
    user = User(
        email=email, hashed_password=unusable_password, role=roles.ADMIN,
        org_id=organization.id, is_active=True, is_verified=True,
    )
    db.add(user)
    await db.flush()
    await audit.record(
        db, "user.signup", org_id=user.org_id, user_id=user.id, request=request,
        details={"email": email, "role": user.role, "via_invite": False, "method": "sso"},
    )
    return user


# ======================================================================
# ENDPOINTS
# ======================================================================


class SSOConfig(BaseModel):
    enabled: bool
    provider_name: str


class SSOExchange(BaseModel):
    code: str


@router.get("/config", response_model=SSOConfig)
async def sso_config():
    """Whether to show the "Continue with SSO" button, and its label."""
    return {"enabled": settings.oidc_configured, "provider_name": settings.OIDC_PROVIDER_NAME}


@router.get(
    "/login",
    dependencies=[Depends(_require_sso), Depends(deps.rate_limit_by_ip("login"))],
)
async def sso_login(request: Request, invite: Optional[str] = None):
    """Start a sign-in: redirect the browser to the identity provider."""
    try:
        discovery = await _discovery()
    except Exception as exc:
        logger.warning("OIDC discovery failed", extra={"error": repr(exc)})
        return _frontend_redirect(error="Single sign-on is unavailable right now. Please try again later.")

    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    binding = secrets.token_urlsafe(24)
    state = jwt.encode(
        {
            "type": "oidc_state",
            "nonce": nonce,
            "verifier": verifier,
            "binding": binding,
            "invite": invite or None,
            "exp": int(time.time()) + STATE_LIFETIME_SECONDS,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    params = {
        "response_type": "code",
        "client_id": settings.OIDC_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "scope": settings.OIDC_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(
        f"{discovery['authorization_endpoint']}?{urlencode(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    # SameSite=Lax: sent on the provider's top-level redirect back to us,
    # never on a cross-site sub-request.
    response.set_cookie(
        STATE_COOKIE,
        binding,
        max_age=STATE_LIFETIME_SECONDS,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        path=f"{settings.API_V1_STR}/auth/sso",
    )
    return response


@router.get("/callback", name="sso_callback", dependencies=[Depends(_require_sso)])
async def sso_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(deps.get_db),
):
    """The provider sends the browser back here after the user signed in."""
    if error:
        # e.g. "access_denied" when the user pressed Cancel at the provider.
        return _frontend_redirect(error="Sign-in was cancelled or refused by the identity provider.")
    if not code or not state:
        return _frontend_redirect(error="The sign-in response was incomplete. Please try again.")

    try:
        claims = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if claims.get("type") != "oidc_state":
            raise jwt.InvalidTokenError("wrong type")
    except jwt.PyJWTError:
        return _frontend_redirect(error="This sign-in link has expired. Please try again.")

    cookie = request.cookies.get(STATE_COOKIE, "")
    if not cookie or not secrets.compare_digest(cookie, str(claims.get("binding", ""))):
        return _frontend_redirect(error="This sign-in was started in a different browser. Please try again.")

    try:
        tokens = await _exchange_code(code, claims["verifier"], _redirect_uri(request))
        id_claims = await _verify_id_token(tokens["id_token"], claims["nonce"])
        email = (id_claims.get("email") or "").strip()
        if not email or "@" not in email:
            raise _SSOError("The identity provider did not share an email address. Check the 'email' scope.")
        if not _email_is_verified(id_claims):
            raise _SSOError("Your identity provider has not verified this email address.")

        user = await _find_or_create_user(db, request, email, claims.get("invite"))
        handoff = await sessions.create_user_token(db, user.id, SSO_HANDOFF, HANDOFF_LIFETIME)
        await db.commit()
    except _SSOError as exc:
        await db.rollback()
        return _frontend_redirect(error=str(exc))
    except Exception as exc:
        await db.rollback()
        logger.warning("SSO callback failed", extra={"error": repr(exc)})
        return _frontend_redirect(error="Single sign-on failed. Please try again.")

    return _frontend_redirect(code=handoff)


@router.post(
    "/exchange",
    response_model=Token,
    dependencies=[Depends(_require_sso), Depends(deps.rate_limit_by_ip("login"))],
)
async def sso_exchange(
    body: SSOExchange,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(deps.get_db),
):
    """Swap the one-time code from the callback for an access + refresh token."""
    row = await sessions.redeem_user_token(db, body.code, SSO_HANDOFF)
    user = await db.get(User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sign-in code is invalid or has expired. Please sign in again.",
        )
    tokens = await sessions.issue_token_pair(db, user)
    await audit.record(
        db, "user.login", org_id=user.org_id, user_id=user.id, request=request,
        details={"method": "sso"},
    )
    await db.commit()
    return tokens
