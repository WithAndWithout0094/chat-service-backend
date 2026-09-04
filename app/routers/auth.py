"""인증 라우터 — 회원가입 / 로그인 / 로그아웃 (/auth/*).

가입·로그인은 반드시 anon 키로 한다. service_role 은 관리자 우회 키라
"사용자로서 로그인"이라는 동작 자체가 성립하지 않는다.

회원가입 시 profiles 행은 우리 코드가 만들지 않는다. auth.users 에 행이 생기면
DB 트리거가 자동으로 만든다.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.cache import cache_delete
from app.db import get_anon_client
from app.deps import CurrentUser, get_current_user, session_cache_key
from app.schemas import LoginRequest, SignupRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse)
def signup(payload: SignupRequest):
    """회원가입. 실패 사유(중복 이메일, 짧은 비밀번호 등)는 Supabase 메시지를 그대로 전달한다."""
    client = get_anon_client()

    try:
        result = client.auth.sign_up(
            {"email": payload.email, "password": payload.password}
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 이메일 확인 설정이 켜져 있으면 세션이 없다 → 토큰 없이 사용자 정보만 응답한다.
    access_token = result.session.access_token if result.session else None

    return TokenResponse(
        access_token=access_token,
        user_id=str(result.user.id),
        email=result.user.email,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    """로그인. 성공하면 이후 모든 인증 요청에 쓸 access_token 을 발급한다."""
    client = get_anon_client()
    try:
        result = client.auth.sign_in_with_password(
            {"email": payload.email, "password": payload.password}
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    return TokenResponse(
        access_token=result.session.access_token,   # 로그인 성공이면 세션이 항상 있다
        user_id=str(result.user.id),
        email=result.user.email,
    )


@router.post("/logout", status_code=204)
def logout(current_user: CurrentUser = Depends(get_current_user)):
    """세션 캐시를 즉시 지운다. TTL 만료를 기다리지 않기 위한 것.

    완전한 로그아웃은 아니다. Supabase 토큰 자체는 만료 시각까지 유효하므로,
    다음 요청은 캐시 미스 상태에서 재검증을 거쳐 다시 통과한다.
    """
    cache_delete(session_cache_key(current_user.token))
