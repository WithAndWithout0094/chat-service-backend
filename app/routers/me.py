"""로그인한 본인의 것만 다루는 라우터 (/me/*).

이 파일의 모든 엔드포인트가 같은 세 가지 규칙을 지킨다.

1. ``Depends(get_current_user)`` 를 받는다 — 로그인 없이는 아무것도 못 부른다(401).
2. user_id 를 요청 본문에서 받지 않는다 — 토큰에서 꺼낸 값만 신뢰하므로 위조가 안 된다.
3. anon 키 클라이언트에 사용자 토큰을 붙여 쓴다 — RLS 가 적용되어, 코드에
   ``where user_id = ...`` 를 쓰지 않아도 DB 가 본인 행만 돌려준다.

conversations.py 는 service_role 키(RLS 우회) 경로이고, 이 파일이 실제 서비스가 쓰는 경로다.

수정·삭제 실패는 "없음"과 "내 것이 아님"을 구분하지 않고 모두 404 로 답한다.
구분하면 그 대화가 존재한다는 사실이 새 나간다.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.cache import cache_delete
from app.db import get_anon_client
from app.deps import CurrentUser, get_current_user
from app.routers.conversations import (   # 캐시 키 규칙은 conversations.py 한 곳에만 둔다
    _conversations_cache_key,
    _messages_cache_key,
)
from app.schemas import (
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
    MyConversationCreate,
    ProfileOut,
    ProfileUpdate,
)

router = APIRouter(prefix="/me", tags=["me"])


# ── 내 정보 ───────────────────────────────────────────────────────────


@router.get("")
def read_me(current_user: CurrentUser = Depends(get_current_user)):
    """내가 누구인지. DB 를 보지 않는다 — 토큰 검증 결과에 이미 신원이 담겨 있다."""
    return {"id": current_user.id, "email": current_user.email}


# ── 내 대화 ───────────────────────────────────────────────────────────


@router.get("/conversations", response_model=list[ConversationOut])
def my_conversations(current_user: CurrentUser = Depends(get_current_user)):
    """내 대화 목록.

    select 에 조건이 한 줄도 없는데 내 것만 돌아온다. postgrest.auth(token) 으로 붙인
    토큰의 주인이 RLS 정책의 auth.uid() 가 되어 DB 쪽에서 걸러 주기 때문이다.
    """
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = (
        client.table("conversations")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


# ── 내 프로필 ─────────────────────────────────────────────────────────


@router.get("/profile", response_model=ProfileOut)
def read_my_profile(current_user: CurrentUser = Depends(get_current_user)):
    """내 프로필. RLS 가 한 행으로 걸러 주므로 조건 없이 조회한다."""
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = client.table("profiles").select("*").execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다")
    return result.data[0]


@router.patch("/profile", response_model=ProfileOut)
def update_my_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """닉네임 수정.

    RLS 가 걸려 있어도 ``.eq("id", ...)`` 는 반드시 필요하다. Supabase 는 WHERE 없는
    UPDATE 문 자체를 막는다(pg-safeupdate). SELECT·DELETE 에는 없는 제약이다.
    """
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = (
        client.table("profiles")
        .update({"username": payload.username})
        .eq("id", current_user.id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다")
    # username 을 담고 있는 캐시가 없으므로 여기서는 무효화할 것이 없다.
    return result.data[0]


# ── 내 대화 만들기 · 이름 변경 · 삭제 ────────────────────────────────


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_my_conversation(
    payload: MyConversationCreate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """내 대화 만들기.

    insert 의 user_id 에 요청 본문 값이 아니라 토큰에서 꺼낸 id 를 넣는다.
    RLS 의 with check 정책이 한 겹 더 막는다.
    """
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = (
        client.table("conversations")
        .insert({"user_id": current_user.id, "title": payload.title})
        .execute()
    )
    cache_delete(_conversations_cache_key(current_user.id))
    return result.data[0]


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201)
def create_my_message(
    conversation_id: UUID,
    payload: MessageCreate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """내 대화에 메시지 넣기.

    소유권은 RLS 의 insert 정책이 확인한다. 남의 대화면 DB 가 거부해 예외가 나고,
    그것을 403 으로 번역한다.
    """
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    try:
        result = (
            client.table("messages")
            .insert(
                {
                    "conversation_id": str(conversation_id),
                    "role": payload.role,
                    "content": payload.content,
                }
            )
            .execute()
        )
    except Exception:
        raise HTTPException(status_code=403, detail="이 대화에 접근할 수 없습니다")
    cache_delete(_messages_cache_key(conversation_id))
    return result.data[0]


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def rename_my_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """내 대화 이름 변경. 남의 행은 RLS 가 막아 0건이 되고, 그것을 404 로 번역한다."""
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = (
        client.table("conversations")
        .update({"title": payload.title})
        .eq("id", str(conversation_id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    cache_delete(_conversations_cache_key(current_user.id))   # 목록에 제목이 실려 있다
    return result.data[0]


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_my_conversation(
    conversation_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """내 대화 삭제. 두 번째 삭제 요청은 0건이 되어 자연스럽게 404 가 된다."""
    client = get_anon_client()
    client.postgrest.auth(current_user.token)
    result = (
        client.table("conversations").delete().eq("id", str(conversation_id)).execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    # 대화 목록과 그 대화의 메시지, 두 캐시가 모두 영향을 받는다.
    cache_delete(_conversations_cache_key(current_user.id))
    cache_delete(_messages_cache_key(conversation_id))
