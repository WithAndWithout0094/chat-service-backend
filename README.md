# 면접 연습 챗봇 — 백엔드

직무를 정하고 면접 질문에 답하며 연습하는 챗봇의 API 서버입니다.
FastAPI + Supabase(PostgreSQL) + Redis + Gemini 로 만들었습니다.

화면(Streamlit)은 별도 저장소에 있습니다: `chat-service-frontend`

## 무엇을 하는 서버인가

- 회원가입·로그인 (Supabase Auth, JWT)
- 대화·메시지 저장 (PostgreSQL, RLS 로 본인 것만 접근)
- 면접관 답변 생성 (Gemini, SSE 스트리밍)
- 세션·메시지 목록 캐싱과 사용 로그·피드백 (Redis)

## 로컬 실행

```bash
cp .env.example .env      # 값을 채웁니다
uv sync
uv run uvicorn app.main:app --reload
```

- API 문서: http://127.0.0.1:8000/docs
- 생존 확인: http://127.0.0.1:8000/health

## 환경변수

`.env.example` 을 참고하세요. Supabase 3개, Gemini 1개, Redis 4개입니다.
`.env` 는 커밋되지 않습니다(`.gitignore`).

## 폴더 구조

```
app/
  main.py            앱 진입점, 라우터 등록
  db.py              Supabase 클라이언트 (service_role / anon)
  redis_client.py    Redis 연결
  cache.py           Redis 장애에 견디는 캐시 함수
  schemas.py         요청·응답 스키마 (Pydantic)
  deps.py            토큰 검증, 대화 소유권 확인
  gemini_client.py   모델 호출과 프롬프트 조립
  routers/
    auth.py            /auth/*           회원가입·로그인·로그아웃
    me.py              /me/*             본인의 대화·프로필
    conversations.py   /conversations/*  메시지 저장·조회 + 캐싱
    chat.py            응답 생성(SSE)·다시 생성·맥락 초기화·피드백·사용 로그
```

## 인증 구조

`/docs` 에서 엔드포인트 오른쪽에 **자물쇠**가 붙은 것은 `Authorization: Bearer <토큰>` 헤더가 필요합니다.
자물쇠가 없는 것은 `/health`, `/auth/signup`, `/auth/login`, `/chat/options` 넷뿐입니다.

소유권 확인은 코드가 직접 비교하지 않습니다. anon 키에 사용자 토큰을 붙여 조회하면
PostgreSQL 의 RLS(Row Level Security) 정책이 본인 행만 돌려줍니다.
없는 대화와 남의 대화는 구분 없이 모두 404 로 답합니다 — 구분하면 그 대화의 존재가 새 나갑니다.

## Render 배포 설정

| 항목 | 값 |
| --- | --- |
| Runtime | `Python 3` |
| Root Directory | **비워 둡니다** (이 저장소의 루트가 곧 백엔드입니다) |
| Build Command | `pip install uv && uv sync` |
| Start Command | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Branch | `main` |

`$PORT` 는 글자 그대로 넣습니다. Render 가 실제 포트를 이 환경변수로 넘겨 줍니다.
고정값(`8000`)을 쓰면 접속이 안 되어 배포가 실패합니다.

환경변수는 `.env.example` 의 8개를 Environment Variables 에 등록합니다.
관리형 Redis 가 TLS 를 요구하면 `REDIS_SSL=true` 를 넣습니다.

배포 후 `https://<이름>.onrender.com/health` 가 `{"status":"ok"}` 를 돌려주면 성공입니다.
