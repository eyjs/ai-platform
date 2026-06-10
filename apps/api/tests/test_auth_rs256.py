"""JWT 비대칭 검증 테스트 (D17, Step 13).

_verify_jwt의 듀얼-모드 동작을 검증한다:
  - RS256: 공개키로만 검증 (개인키는 bff에만)
  - HS256: 과도기 폴백이 켜진 경우에만 공유 시크릿으로
  - 그 외 알고리즘/키-알고리즘 교차는 거부 (알고리즘 혼동 공격 차단)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.gateway.auth import AuthError, AuthService

SECRET = "test-hs256-secret"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """테스트용 RSA 키페어 (private_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _service(public_key: str = "", hs256_fallback: bool = True) -> AuthService:
    return AuthService(
        pool=MagicMock(),
        jwt_secret=SECRET,
        jwt_public_key=public_key,
        jwt_hs256_fallback=hs256_fallback,
    )


_PAYLOAD = {"sub": "user-1", "role": "ADMIN", "security_level_max": "INTERNAL"}


class TestRS256:
    def test_valid_rs256_token_accepted(self, keypair):
        private_pem, public_pem = keypair
        token = pyjwt.encode(_PAYLOAD, private_pem, algorithm="RS256",
                             headers={"kid": "test-kid"})

        ctx = _service(public_key=public_pem)._verify_jwt(token)

        assert ctx.user_id == "user-1"
        assert ctx.user_role == "ADMIN"

    def test_rs256_with_wrong_key_rejected(self, keypair):
        _, public_pem = keypair
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        token = pyjwt.encode(_PAYLOAD, other_pem, algorithm="RS256")

        with pytest.raises(AuthError, match="유효하지 않은 토큰"):
            _service(public_key=public_pem)._verify_jwt(token)

    def test_rs256_without_public_key_rejected(self, keypair):
        private_pem, _ = keypair
        token = pyjwt.encode(_PAYLOAD, private_pem, algorithm="RS256")

        with pytest.raises(AuthError, match="공개키가 설정되지"):
            _service(public_key="")._verify_jwt(token)


class TestHS256Transition:
    def test_hs256_accepted_with_fallback_on(self, keypair):
        _, public_pem = keypair
        token = pyjwt.encode(_PAYLOAD, SECRET, algorithm="HS256")

        ctx = _service(public_key=public_pem, hs256_fallback=True)._verify_jwt(token)

        assert ctx.user_id == "user-1"

    def test_hs256_rejected_with_fallback_off(self, keypair):
        _, public_pem = keypair
        token = pyjwt.encode(_PAYLOAD, SECRET, algorithm="HS256")

        with pytest.raises(AuthError, match="더 이상 허용되지"):
            _service(public_key=public_pem, hs256_fallback=False)._verify_jwt(token)

    def test_legacy_mode_unchanged_without_public_key(self):
        """공개키 미설정(레거시 배포)에서는 HS256이 기존대로 동작."""
        token = pyjwt.encode(_PAYLOAD, SECRET, algorithm="HS256")

        ctx = _service()._verify_jwt(token)

        assert ctx.user_id == "user-1"


class TestAlgorithmConfusion:
    def test_hs256_signed_with_public_key_as_secret_rejected(self, keypair):
        """공개키를 HS256 시크릿으로 쓴 위조 토큰 — 키-알고리즘 교차 차단.

        고전적 알고리즘 혼동 공격: 공격자가 alg=HS256으로 바꾸고 공개키(공개정보)를
        HMAC 시크릿으로 서명. PyJWT는 PEM을 HMAC 키로 쓰는 걸 막으므로 라이브러리
        없이 직접 위조 토큰을 조립한다. 검증측은 alg=HS256 → 공유 시크릿으로만
        검증하므로 서명 불일치로 거부되어야 한다.
        """
        import base64
        import hashlib
        import hmac
        import json

        _, public_pem = keypair

        def _b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps(_PAYLOAD).encode())
        signing_input = f"{header}.{body}".encode()
        # 공개키를 HMAC 시크릿으로 사용한 위조 서명
        sig = hmac.new(public_pem.encode(), signing_input, hashlib.sha256).digest()
        forged = f"{header}.{body}.{_b64(sig)}"

        with pytest.raises(AuthError, match="유효하지 않은 토큰"):
            _service(public_key=public_pem)._verify_jwt(forged)

    def test_unsupported_algorithm_rejected(self, keypair):
        _, public_pem = keypair
        token = pyjwt.encode(_PAYLOAD, SECRET, algorithm="HS512")

        with pytest.raises(AuthError, match="지원하지 않는"):
            _service(public_key=public_pem)._verify_jwt(token)

    def test_garbage_token_rejected(self, keypair):
        _, public_pem = keypair
        with pytest.raises(AuthError, match="유효하지 않은 토큰"):
            _service(public_key=public_pem)._verify_jwt("not-a-jwt")
