"""요청·응답 스키마 (Pydantic).

FastAPI 가 이 모델들로 요청 본문을 검증하고(어긋나면 라우터 실행 전에 422),
``response_model`` 로 응답을 걸러 낸다.

생성용·수정용·응답용을 나눠 두는 이유: 생성 요청에는 id·created_at 이 없고
응답에는 있다. 한 모델로 합치면 둘 중 하나가 어긋난다.

여기서 거는 제약(길이, 허용값)은 DB 제약과 짝을 맞춘 1차 방어선이다.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ── 대화 (conversations) ──────────────────────────────────────────────


class ConversationCreate(BaseModel):
    """POST /conversations 요청 본문."""

    user_id: UUID
    title: str = Field(min_length=1, max_length=100)   # 100 은 DB 컬럼 길이와 맞춘 값


class ConversationOut(BaseModel):
    """대화 한 건의 응답. DB 에 있는 updated_at 은 여기 없으므로 응답에서 걸러진다."""

    id: UUID
    user_id: UUID
    title: str
    created_at: datetime      # 응답에서는 ISO 8601 문자열로 자동 변환된다


class ConversationUpdate(BaseModel):
    """PATCH /conversations/{id} 및 PATCH /me/conversations/{id} 요청 본문."""

    title: str = Field(min_length=1, max_length=100)


# ── 메시지 (messages) ─────────────────────────────────────────────────


class MessageCreate(BaseModel):
    """메시지 저장 요청 본문.

    role 을 Literal 로 좁혀 두면 "robot" 같은 값이 DB 까지 가지 않고 422 로 끝난다.
    """

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)


class MessageOut(BaseModel):
    """메시지 한 건의 응답."""

    id: UUID
    conversation_id: UUID
    role: str                 # DB 에서 온 값이라 Literal 로 다시 좁히지 않는다
    content: str
    created_at: datetime


# ── 인증 (auth) ───────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    """POST /auth/signup 요청 본문.

    EmailStr 을 쓰지 않는 이유: Supabase Auth 가 이미 형식과 중복을 검사한다.
    이중으로 막기보다 Supabase 의 실패 사유를 그대로 400 으로 전달하는 편이 낫다.
    """

    email: str
    password: str


class LoginRequest(BaseModel):
    """POST /auth/login 요청 본문."""

    email: str
    password: str


class TokenResponse(BaseModel):
    """가입·로그인 응답. access_token 을 이후 요청의 Authorization 헤더에 싣는다.

    access_token 이 None 일 수 있는 이유: Supabase 의 이메일 확인 설정이 켜져 있으면
    가입 직후 세션이 만들어지지 않는다.
    """

    access_token: str | None
    user_id: str
    email: str


# ── 프로필 (profiles) ─────────────────────────────────────────────────


class ProfileOut(BaseModel):
    """GET·PATCH /me/profile 응답."""

    id: UUID                  # auth.users.id 와 같은 값
    username: str
    created_at: datetime


class ProfileUpdate(BaseModel):
    """PATCH /me/profile 요청 본문."""

    username: str = Field(min_length=2, max_length=30)


# ── 내 대화 (me) ──────────────────────────────────────────────────────


class MyConversationCreate(BaseModel):
    """POST /me/conversations 요청 본문.

    ConversationCreate 와 달리 user_id 가 없다. 소유자는 본문이 아니라 토큰에서 꺼내므로
    남의 user_id 를 넣는 위조가 성립하지 않는다.
    """

    title: str | None = None


class ConversationUpdate2(BaseModel):
    """미사용. 대화 이름 변경은 위 ConversationUpdate 를 재사용한다."""

    title: str


# ── 응답 생성 (chat) ──────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """POST /conversations/{id}/chat 요청 본문.

    tone/length 의 기본 문자열을 여기 적지 않는다. 기본값은 gemini_client.py 의
    DEFAULT_TONE / DEFAULT_LENGTH 한 곳에서만 관리한다.
    """

    content: str
    tone: str | None = None
    length: str | None = None


class RegenerateRequest(BaseModel):
    """POST /conversations/{id}/regenerate 요청 본문.

    ChatRequest 를 재사용하지 않는 이유: 다시 생성은 질문을 보내지 않는데
    ChatRequest 는 content 가 필수라 422 가 난다. 필수 항목이 다르면 스키마도 다르다.
    """

    tone: str | None = None
    length: str | None = None


class FeedbackRequest(BaseModel):
    """POST /conversations/{id}/feedback 요청 본문.

    value 가 None 이면 평가 취소다. 되돌릴 수 없는 평가는 아무도 누르지 않는다.
    """

    message_id: UUID
    value: Literal["up", "down"] | None = None
