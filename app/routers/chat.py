"""면접관 응답 생성 라우터.

엔드포인트

    GET  /chat/options                       화면에 그릴 말투·길이 선택지와 기억 범위
    POST /conversations/{id}/chat            답변 생성 (SSE 스트리밍)
    POST /conversations/{id}/regenerate      마지막 답변을 지우고 다시 생성 (SSE 스트리밍)
    POST /conversations/{id}/reset-context   맥락 초기화
    POST /conversations/{id}/feedback        도움됨 / 아쉬움 저장·취소
    GET  /conversations/{id}/feedback        평가 상태 전체 조회
    GET  /conversations/{id}/usage-logs      요청 시각·소요 시간·토큰 수

기록과 기억은 다르다. 기록은 messages 테이블에 남는 것으로 지우지 않는다.
기억은 모델에게 보내는 것으로, 마지막 초기화 지점 이후의 최근 MAX_HISTORY_MESSAGES 개뿐이다.
전부 보내면 느리고, 비싸고, 옛 대화에 끌려간다.

SSE(Server-Sent Events)는 한 번의 HTTP 응답을 여러 조각으로 나눠 밀어 보내는 방식이다.

    data: {"text": "안녕하세요"}
    data: {"done": true, "message_id": "..."}

조각을 JSON 으로 감싸는 이유는 답변 안의 줄바꿈 때문이다. SSE 는 빈 줄을 이벤트의 끝으로
약속하므로 줄바꿈을 날것으로 흘리면 형식이 깨진다. 또 스트림이 시작되면 헤더가 이미 나가
상태 코드를 바꿀 수 없으므로, 도중의 실패는 error 이벤트로 알린다.

/chat/options 를 뺀 모든 라우트가 Depends(require_own_conversation) 을 받는다.
함수 안에서 검사하지 않고 의존성으로 뺀 이유는, 시그니처만 봐도 걸렸는지 보이게 하기 위해서다.
"""

import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from google.genai import types
from redis.exceptions import RedisError

from app.cache import cache_delete
from app.deps import require_own_conversation
from app.db import supabase
from app.gemini_client import (
    DEFAULT_LENGTH,
    DEFAULT_TONE,
    GEMINI_MODEL,
    LENGTHS,
    TONES,
    build_system_prompt,
    client,
)
# 사용 로그(리스트)와 피드백(해시)은 cache.py 가 감싸지 않는 자료구조 명령을 쓰므로
# 이 파일이 Redis 연결을 직접 만진다. 대신 RedisError 를 여기서 직접 잡는다.
from app.redis_client import r
from app.routers.conversations import (
    _messages_cache_key,
    create_message,
    fetch_all_messages,
)
from app.schemas import (
    ChatRequest,
    FeedbackRequest,
    MessageCreate,
    MessageOut,
    RegenerateRequest,
)

logger = logging.getLogger(__name__)

# 경로 접두사가 달라 라우터를 둘로 나눈다.
router = APIRouter(prefix="/conversations", tags=["chat"])
options_router = APIRouter(prefix="/chat", tags=["chat"])

# 모델에 보낼 최근 메시지 수(사용자+면접관 합계). 대략 10번 주고받은 분량.
# 화면은 이 숫자를 하드코딩하지 않고 /chat/options 로 받아 간다.
MAX_HISTORY_MESSAGES = 20

# 대화당 Redis 에 남길 사용 로그 건수.
MAX_USAGE_LOGS = 50

# 맥락을 끊는 표시. role="system" 으로 저장하면 화면에는 보이면서 모델에는 가지 않고
# (아래 _ROLE_MAP 에 없다), DB 스키마도 건드리지 않아도 된다.
CONTEXT_RESET_MARKER = "[맥락 초기화] 이 지점 이전은 면접관이 기억하지 않습니다."

# 우리 DB 의 role 을 Gemini 의 role 로 바꾸는 표. 여기 없는 role(system)은 보내지 않는다.
# Gemini 문서가 인정하는 값은 USER 와 MODEL 뿐이다. assistant 가 지금 통과한다고 해서
# 문서에 없는 동작에 기대지 않는다.
_ROLE_MAP = {"user": "user", "assistant": "model"}


# ── 선택지 ────────────────────────────────────────────────────────────


@options_router.get("/options")
def chat_options():
    """화면이 그릴 선택지와 기억 범위를 내려 준다.

    선택지는 gemini_client.py 의 TONES/LENGTHS, 기억 범위는 이 파일의
    MAX_HISTORY_MESSAGES 한 곳에서만 관리한다. 화면에 하드코딩하면 한쪽만 고쳤을 때
    버튼은 있는데 효과가 없거나, 안내 숫자가 실제와 달라진다.
    """
    return {
        "tones": list(TONES),          # 라벨(키)만 보낸다. 프롬프트 문장은 서버에만 있다
        "lengths": list(LENGTHS),
        "default_tone": DEFAULT_TONE,
        "default_length": DEFAULT_LENGTH,
        "max_history_messages": MAX_HISTORY_MESSAGES,
    }


# ── 직무 조회 · 기억 조립 ─────────────────────────────────────────────


def _job_title(conversation_id: UUID) -> str:
    """대화 제목을 지원 직무로 읽어 온다.

    사용자가 사이드바의 '직무' 칸에 넣은 값이 conversations.title 로 저장되는 규칙이다.
    """
    result = (
        supabase.table("conversations")
        .select("title")
        .eq("id", str(conversation_id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    return result.data[0]["title"] or "지원 직무 미지정"


def _build_history(conversation_id: UUID) -> list[dict]:
    """모델에게 보낼 이전 대화를 Gemini 의 contents 형식으로 만든다.

    세 단계를 이 순서로 적용한다. 순서가 바뀌면 결과가 달라진다.

    1. 마지막 초기화 지점(role=system) 이후만 남긴다
    2. 모델이 모르는 role 을 뺀다
    3. 최근 MAX_HISTORY_MESSAGES 개만 남긴다

    3 을 1 보다 먼저 하면, 최근 20개 안에 초기화 지점이 없을 때 끊었던 옛 대화가 다시 딸려 온다.

    화면의 기억 개수 표시(streamlit_app.py 의 _remembered_count)가 같은 순서로 센다.
    한쪽만 고치면 화면의 안내가 실제와 어긋난다.
    """
    messages, _ = fetch_all_messages(conversation_id)

    for index in range(len(messages) - 1, -1, -1):     # 뒤에서 앞으로
        if messages[index]["role"] == "system":
            messages = messages[index + 1 :]
            break

    usable = [m for m in messages if m["role"] in _ROLE_MAP]
    recent = usable[-MAX_HISTORY_MESSAGES:]

    return [
        {"role": _ROLE_MAP[m["role"]], "parts": [{"text": m["content"]}]}
        for m in recent
    ]


# ── 사용 로그 · 피드백 ────────────────────────────────────────────────


def _usage_log_key(conversation_id: UUID) -> str:
    """사용 로그(Redis 리스트) 키."""
    return f"usage_log:{conversation_id}"


def _feedback_key(conversation_id: UUID) -> str:
    """피드백(Redis 해시) 키."""
    return f"feedback:{conversation_id}"


def _log_usage(conversation_id: UUID, started_at: float, usage) -> None:
    """요청 시각·소요 시간·토큰 사용량을 Redis 리스트에 남긴다.

    서비스 데이터가 아니라 운영 기록이라 Postgres 가 아닌 Redis 에 둔다.
    지워져도 서비스는 돌아간다. 실제 서비스라면 로그 수집 도구로 보낸다.

    이 함수는 스트림 제너레이터 안에서 불린다. 예외가 튀면 마지막 done 이벤트가 못 나가
    화면이 끝났는지를 모르게 되므로, RedisError 를 여기서 삼킨다.
    """
    entry = {
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": round((time.monotonic() - started_at) * 1000),
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "response_tokens": getattr(usage, "candidates_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }
    key = _usage_log_key(conversation_id)
    try:
        r.lpush(key, json.dumps(entry))          # 최신이 맨 앞
        r.ltrim(key, 0, MAX_USAGE_LOGS - 1)      # 최근 N 건만 유지
    except RedisError as error:
        logger.warning("사용 로그 저장 실패 (%s): %s", key, error)


@router.get("/{conversation_id}/usage-logs")
def usage_logs(conversation_id: UUID = Depends(require_own_conversation)):
    """이 대화의 사용 로그를 최신순으로 돌려준다. Redis 가 죽었으면 빈 목록."""
    key = _usage_log_key(conversation_id)
    try:
        raw = r.lrange(key, 0, MAX_USAGE_LOGS - 1)
    except RedisError as error:
        logger.warning("사용 로그 조회 실패 (%s): %s", key, error)
        return []
    return [json.loads(item) for item in raw]


@router.post("/{conversation_id}/feedback")
def save_feedback(
    payload: FeedbackRequest,
    conversation_id: UUID = Depends(require_own_conversation),
):
    """답변 평가를 저장하거나 취소한다. value 가 None 이면 취소다.

    리스트가 아니라 해시를 쓰는 이유: 메시지 하나에 값 하나이고 다시 누르면 덮어써야 한다.
    리스트로 쌓으면 마지막 값을 찾으려 전체를 훑어야 한다.

    사용 로그와 달리 이것은 사용자가 직접 누른 결과다. 저장 실패를 조용히 넘기면
    눌린 표시가 사라져 버그로 보이므로 500 으로 알린다.
    """
    key = _feedback_key(conversation_id)
    try:
        if payload.value is None:
            r.hdel(key, str(payload.message_id))
        else:
            r.hset(key, str(payload.message_id), payload.value)
    except RedisError as error:
        logger.warning("피드백 저장 실패 (%s): %s", key, error)
        raise HTTPException(status_code=500, detail="피드백을 저장하지 못했습니다. 잠시 후 다시 시도하세요.")
    return {"message_id": str(payload.message_id), "value": payload.value}


@router.get("/{conversation_id}/feedback")
def read_feedback(conversation_id: UUID = Depends(require_own_conversation)):
    """평가 상태 전체를 돌려준다. 화면이 버튼의 눌린 상태를 그리는 데 쓴다."""
    key = _feedback_key(conversation_id)
    try:
        return r.hgetall(key)
    except RedisError as error:
        logger.warning("피드백 조회 실패 (%s): %s", key, error)
        return {}                # 못 읽으면 아무것도 안 눌린 것으로 그린다


# ── 맥락 초기화 ───────────────────────────────────────────────────────


@router.post("/{conversation_id}/reset-context", response_model=MessageOut)
def reset_context(conversation_id: UUID = Depends(require_own_conversation)):
    """맥락을 끊는다. 메시지를 지우지 않고 표시 한 건을 추가할 뿐이다.

    실행 후 메시지 목록은 줄지 않고 한 건 늘어난다. 줄어드는 것은 모델이 참고하는 범위뿐이다.
    """
    return create_message(
        conversation_id, MessageCreate(role="system", content=CONTEXT_RESET_MARKER)
    )


# ── 응답 생성 (스트리밍) ──────────────────────────────────────────────


def _stream_answer(conversation_id: UUID, contents: list, system_prompt: str):
    """모델 응답을 조각으로 흘려보내고, 다 받은 뒤 한 번에 저장한다.

    조각마다 저장하면 메시지가 수십 개로 쪼개진다. 저장은 마지막에 한 번뿐이고,
    message_id 는 저장 후에야 생기므로 done 이벤트에 담아 마지막에 보낸다.

    실패를 raise 하지 않고 error 이벤트로 보내는 이유는, 스트림이 시작된 뒤에는
    HTTP 상태 코드를 바꿀 수 없기 때문이다.
    """

    def event_stream():
        started_at = time.monotonic()
        full_text = ""       # 조각을 모아 둘 곳. yield 로 멈춰 있는 동안에도 살아 있다
        last_usage = None    # 토큰 사용량은 마지막 조각에 실려 온다
        try:
            for chunk in client.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            ):
                if chunk.text:
                    full_text += chunk.text
                    yield "data: " + json.dumps({"text": chunk.text}) + "\n\n"
                if chunk.usage_metadata:
                    last_usage = chunk.usage_metadata
        except Exception as e:
            # 429 한도 초과, 잘못된 API 키, 없는 모델 등. 원인 문자열을 그대로 보존한다.
            yield "data: " + json.dumps({"error": f"{type(e).__name__}: {e}"}) + "\n\n"
            return

        if not full_text:
            # 안전 필터에 걸리면 예외가 아니라 빈 텍스트로 온다. 빈 답변을 저장하면 안 된다.
            yield "data: " + json.dumps({"error": "모델이 빈 응답을 돌려주었습니다. 질문을 바꿔서 다시 시도하세요."}) + "\n\n"
            return

        saved = create_message(
            conversation_id, MessageCreate(role="assistant", content=full_text)
        )
        _log_usage(conversation_id, started_at, last_usage)
        yield "data: " + json.dumps({"done": True, "message_id": saved["id"]}) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{conversation_id}/chat")
def chat(
    payload: ChatRequest,
    conversation_id: UUID = Depends(require_own_conversation),
):
    """면접관 답변을 만들어 조각의 흐름으로 돌려준다.

    돌려주는 것이 완성된 메시지가 아니라 스트림이라 response_model 을 붙이지 않는다.

    이력 조립과 사용자 메시지 저장을 제너레이터 밖에서 먼저 끝낸다. 안에 두면
    클라이언트가 스트림을 끝까지 안 받았을 때 사용자가 쓴 답변이 저장되지 않는다.
    """
    job_title = _job_title(conversation_id)

    # 이력을 사용자 메시지 저장보다 먼저 만든다. 순서를 바꾸면 방금 쓴 질문이
    # 이력에도 들어가 같은 말을 두 번 보내게 된다.
    history = _build_history(conversation_id)

    create_message(conversation_id, MessageCreate(role="user", content=payload.content))

    contents = history + [{"role": "user", "parts": [{"text": payload.content}]}]

    return _stream_answer(
        conversation_id,
        contents,
        build_system_prompt(job_title, payload.tone, payload.length),
    )


@router.post("/{conversation_id}/regenerate")
def regenerate(
    payload: RegenerateRequest,
    conversation_id: UUID = Depends(require_own_conversation),
):
    """마지막 답변을 지우고 다시 만든다.

    다시 시도와 다르다. 다시 시도는 실패한 요청을 그대로 보내는 것이라 지울 답이 없고,
    화면이 처리한다. 여기는 성공한 답변이 마음에 안 들 때다.

    기존 답을 먼저 지워야 한다. 안 지우면 같은 질문에 답이 둘 쌓이고, 이력 전달이
    그 둘을 모두 실어 보내 대화가 이상해진다.

    질문을 다시 받지 않는 이유는, 삭제 후 이력을 다시 조립하면 마지막 사용자 질문까지만
    들어오므로 그 이력이 곧 질문이기 때문이다.
    """
    messages, _ = fetch_all_messages(conversation_id)
    if not messages or messages[-1]["role"] != "assistant":
        raise HTTPException(status_code=400, detail="다시 생성할 답변이 없습니다.")

    supabase.table("messages").delete().eq("id", messages[-1]["id"]).execute()
    cache_delete(_messages_cache_key(conversation_id))   # 지워야 삭제가 목록에 반영된다

    job_title = _job_title(conversation_id)
    history = _build_history(conversation_id)
    if not history:
        raise HTTPException(status_code=400, detail="다시 생성할 질문이 없습니다.")

    return _stream_answer(
        conversation_id,
        history,
        build_system_prompt(job_title, payload.tone, payload.length),
    )
