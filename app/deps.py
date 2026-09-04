"""요청을 보낸 사람이 누구인지 알아내는 의존성 모음.

``Authorization: Bearer <토큰>`` 헤더를 Supabase Auth 로 검증하고, 결과를 Redis 에
짧게 캐싱해 두 번째 요청부터는 왕복을 생략한다.

라우터는 이 파일의 함수를 ``Depends()`` 로 받는다.

- ``get_current_user``        로그인 여부만 확인한다 (실패 시 401)
- ``require_own_conversation`` 로그인 + 그 대화가 본인 것인지까지 확인한다 (아니면 404)
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.cache import cache_get, cache_set
from app.db import get_anon_client

# HTTPBearer 를 쓰면 OpenAPI 의 security 항목이 되어 /docs 에 Authorize 버튼과 자물쇠가 붙는다.
# Header(...) 로 직접 받으면 단순 파라미터가 되어 /docs 에서 토큰을 넣어 볼 수 없다.
bearer_scheme = HTTPBearer()

# 세션 캐시 수명. 로그인 상태는 자주 바뀌지 않고, 로그아웃은 auth.py 가 캐시를 즉시 지운다.
SESSION_CACHE_TTL_SECONDS = 300


@dataclass
class CurrentUser:
    """검증이 끝난 현재 사용자.

    ``token`` 을 함께 들고 다니는 이유: me.py 가 ``client.postgrest.auth(token)`` 으로
    RLS 를 적용하고, auth.py 의 로그아웃이 세션 캐시 키를 재현하는 데 쓴다.
    """

    id: str
    email: str
    token: str


def session_cache_key(token: str) -> str:
    """세션 캐시 키를 만든다.

    토큰 원문을 키에 넣지 않는다. Redis 키 목록을 볼 수 있는 사람이 곧 그 계정으로
    로그인할 수 있게 되기 때문이다. 저장(get_current_user)과 삭제(auth.logout)가
    같은 키를 써야 하므로 규칙을 이 함수 한 곳에 둔다.
    """
    return f"session:{hashlib.sha256(token.encode()).hexdigest()}"


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    """토큰을 검증해 CurrentUser 를 돌려준다. 실패하면 401."""
    token = credentials.credentials     # HTTPBearer 가 "Bearer " 접두어를 이미 떼어냈다
    cache_key = session_cache_key(token)

    cached = cache_get(cache_key)
    if cached:
        data = json.loads(cached)
        return CurrentUser(id=data["id"], email=data["email"], token=token)

    client = get_anon_client()
    try:
        result = client.auth.get_user(token)
    except Exception:
        # 실패는 캐시하지 않는다. 캐시하면 토큰이 다시 유효해져도 TTL 동안 계속 막힌다.
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    current_user = CurrentUser(id=str(result.user.id), email=result.user.email, token=token)

    # 토큰은 캐시에 넣지 않는다. 요청 헤더로 매번 오므로 저장할 이유가 없고, 유출 위험만 는다.
    cache_set(
        cache_key,
        json.dumps({"id": current_user.id, "email": current_user.email}),
        SESSION_CACHE_TTL_SECONDS,
    )
    return current_user


def require_own_conversation(
    conversation_id: UUID, current_user: CurrentUser = Depends(get_current_user)
) -> UUID:
    """이 대화가 본인 것인지 확인하고 conversation_id 를 그대로 돌려준다. 아니면 404.

    소유자를 코드로 비교하지 않는다. anon 키 + 사용자 토큰으로 조회해서 0건이면
    "없거나 내 것이 아니다"이고, RLS 정책이 그 판단을 대신한다.

    없는 대화와 남의 대화를 구분하지 않고 둘 다 404 로 답한다. 구분하면
    "그 대화는 존재한다"는 정보가 새 나간다.

    ``get_current_user`` 를 안에서 다시 Depends 로 받으므로, 라우터에 이 함수 하나만
    걸어도 토큰 검증(401) → 소유권 확인(404) 순으로 실행된다.
    """
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    owned = (
        client.table("conversations")
        .select("id")
        .eq("id", str(conversation_id))
        .execute()
    )
    if not owned.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation_id
