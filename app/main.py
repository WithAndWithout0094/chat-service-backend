"""FastAPI 앱 진입점.

실행:
    cd backend
    uv run uvicorn app.main:app --reload

라우터 구성

    /auth/*             회원가입·로그인·로그아웃              routers/auth.py
    /me/*               로그인한 본인의 것만 다루는 API       routers/me.py
    /conversations/*    메시지 CRUD + 캐싱                    routers/conversations.py
    /conversations/{id}/chat 등, /chat/options
                        면접관 응답 생성(SSE)·피드백·사용 로그  routers/chat.py

공용 모듈

    db.py             Supabase 클라이언트 (service_role / anon)
    redis_client.py   Redis 연결
    cache.py          Redis 장애에 견디는 캐시 함수
    schemas.py        요청·응답 스키마 (Pydantic)
    deps.py           토큰 검증과 대화 소유권 확인
    gemini_client.py  Gemini 호출과 프롬프트 조립
"""

from fastapi import FastAPI

from app.routers import auth, chat, conversations, me

app = FastAPI(title="chat-service", version="0.1.0")

app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(chat.options_router)   # 같은 파일의 두 번째 라우터 (/chat 접두사)
app.include_router(auth.router)
app.include_router(me.router)


@app.get("/health")
def health():
    """생존 확인용. DB·Redis 를 거치지 않으므로 항상 빠르다."""
    return {"status": "ok"}
