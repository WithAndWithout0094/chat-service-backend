"""메시지 저장·조회 라우터 (/conversations/*) 와 Redis 캐싱.

라우트와 함수를 나눠 둔다. ``create_message`` / ``fetch_all_messages`` 는 chat.py 가
파이썬에서 직접 부르는 함수이고, ``post_message`` / ``get_messages`` 는 그것을 감싼
HTTP 라우트다. 저장 로직을 두 벌로 두면 캐시 무효화를 한쪽에서 빠뜨린다.

DB 접근은 service_role 키라 RLS 를 우회한다. 그래서 "내 대화인가"는 라우트 앞단의
``require_own_conversation`` 이 anon 키 + 사용자 토큰으로 따로 확인한다.

조회는 cache-aside 방식이다.
    캐시에 있으면 그대로 반환 → 없으면 DB 조회 → 결과를 TTL 과 함께 캐시에 저장.
쓰기(insert/delete)를 하는 코드가 해당 캐시 키를 직접 지운다. TTL 은 안전망이다.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.cache import cache_delete, cache_get, cache_set
from app.db import supabase
from app.deps import require_own_conversation
from app.schemas import MessageCreate, MessageOut

router = APIRouter(prefix="/conversations", tags=["conversations"])

# 메시지는 대화 중 계속 바뀌고 사용자가 바로 알아채므로 짧게 잡는다.
MESSAGES_CACHE_TTL_SECONDS = 30

# 대화 목록은 만들거나 지울 때만 바뀐다. 이 키의 무효화는 me.py 가 담당한다.
CONVERSATIONS_CACHE_TTL_SECONDS = 60


def _messages_cache_key(conversation_id: UUID) -> str:
    """메시지 목록 캐시 키. 저장·삭제하는 모든 곳이 같은 키를 쓰도록 여기서만 만든다."""
    return f"messages:{conversation_id}"


def _conversations_cache_key(user_id) -> str:
    """대화 목록 캐시 키. me.py 가 대화를 만들·바꾸·지울 때 무효화용으로 쓴다."""
    return f"conversations:{user_id}"


# ── 함수 (chat.py 가 직접 부른다) ─────────────────────────────────────


def create_message(conversation_id: UUID, payload: MessageCreate):
    """메시지 한 건을 저장하고 메시지 캐시를 무효화한다.

    user·assistant·system 메시지가 전부 이 함수를 거치므로 무효화를 빠뜨릴 수 없다.
    무효화를 빼면 "방금 보냈는데 목록에 안 보이는" 상태가 TTL 동안 이어진다.
    """
    conversation = (
        supabase.table("conversations")
        .select("id")
        .eq("id", str(conversation_id))
        .execute()
    )
    if not conversation.data:
        raise HTTPException(status_code=404, detail="conversation not found")

    result = (
        supabase.table("messages")
        .insert(
            {
                "conversation_id": str(conversation_id),
                "role": payload.role,
                "content": payload.content,
            }
        )
        .execute()
    )

    cache_delete(_messages_cache_key(conversation_id))
    return result.data[0]


def fetch_all_messages(conversation_id: UUID) -> tuple[list[dict], bool]:
    """이 대화의 전체 메시지를 시간순으로 가져온다. 반환: (메시지 목록, 캐시 적중 여부).

    라우트와 분리한 이유: chat.py 의 이력 조립이 이 조회를 파이썬 함수로 직접 써야 하는데,
    HTTP 라우트에는 인증 의존성과 페이지 자르기가 붙어 있어 그대로 부를 수 없다.

    적중 여부를 함께 돌려주는 것은 라우트가 X-Cache 헤더를 달 수 있게 하기 위해서다.
    """
    cache_key = _messages_cache_key(conversation_id)

    cached = cache_get(cache_key)
    if cached:
        return json.loads(cached), True

    result = (
        supabase.table("messages")
        .select("*")
        .eq("conversation_id", str(conversation_id))
        .order("created_at", desc=False)
        .execute()
    )
    # created_at 이 datetime 이라 json.dumps 가 거부한다. default=str 로 문자열화한다.
    cache_set(cache_key, json.dumps(result.data, default=str), MESSAGES_CACHE_TTL_SECONDS)
    return result.data, False


# ── HTTP 라우트 ───────────────────────────────────────────────────────


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
def post_message(
    payload: MessageCreate,
    conversation_id: UUID = Depends(require_own_conversation),
):
    """메시지 저장.

    의존성이 토큰 검증(401)과 소유권 확인(404)을 먼저 처리한 뒤 conversation_id 를 넘겨 준다.
    기본값이 있는 인자는 뒤에 와야 하므로 payload 가 앞이다.
    """
    return create_message(conversation_id, payload)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    response: Response,
    conversation_id: UUID = Depends(require_own_conversation),
    limit: int = 20,
    offset: int = 0,
):
    """메시지 목록. 캐시로 답했는지를 X-Cache 응답 헤더로 알려 준다.

    limit 기본값 20 은 "앞에서부터 20건"이다. 대화 전체를 그려야 하는 클라이언트는
    ?limit=500 처럼 넉넉히 넘겨야 뒷부분이 잘리지 않는다.
    """
    messages, hit = fetch_all_messages(conversation_id)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return messages[offset : offset + limit]
