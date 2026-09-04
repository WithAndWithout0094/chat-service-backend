"""Supabase(PostgreSQL) 클라이언트를 만드는 곳.

키를 두 종류로 나눠 쓴다.

- ``supabase``          service_role 키. RLS(Row Level Security)를 우회한다.
                        서버 내부 전용 경로에서만 쓴다. (conversations.py, chat.py)
- ``get_anon_client()`` anon 키. 사용자 토큰과 짝지어 쓰면 RLS 가 적용된다.
                        "그 사용자로서" DB 에 접근하는 경로. (deps.py, auth.py, me.py)

RLS 가 켜져 있으면 ``where user_id = ...`` 를 코드에 쓰지 않아도 DB 가 본인 행만 돌려준다.
"""

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

# os.environ[...] 은 값이 없으면 KeyError 로 죽는다. 설정 누락을 서버 기동 시점에 드러내려는 의도.
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # 외부 노출 금지
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

# 관리자 클라이언트는 서버 전체가 하나를 공유한다 (요청마다 달라질 것이 없다).
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def get_anon_client() -> Client:
    """anon 키 클라이언트를 새로 만들어 돌려준다.

    공유하지 않고 매번 새로 만드는 이유: 호출한 쪽이 ``client.postgrest.auth(token)`` 으로
    사용자 토큰을 붙여 쓴다. 하나를 공유하면 A 의 토큰이 붙은 클라이언트로 B 의 요청을
    처리하는 사고가 난다.
    """
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
