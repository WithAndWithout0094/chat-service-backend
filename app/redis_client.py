"""Redis 연결 객체.

세션·메시지 목록 캐시와 사용 로그·피드백 저장에 쓴다.
Redis 는 원본이 아니라 사본이므로, 죽어도 서비스는 살아야 한다.
그래서 이 객체를 직접 만지는 곳은 예외를 감싸 주는 cache.py 와,
cache.py 가 다루지 않는 자료구조 명령(LPUSH/HSET 등)을 쓰는 routers/chat.py 뿐이다.
"""

import os

import redis
from dotenv import load_dotenv

load_dotenv()

# 실제 TCP 연결은 여기가 아니라 첫 명령을 보낼 때 맺어진다.
r = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),          # 환경변수는 문자열이라 int 변환이 필요하다
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=True,                       # 결과를 bytes 가 아니라 str 로 받는다
    # 관리형 Redis 가 TLS 를 요구하면 .env 에 REDIS_SSL=true 를 넣는다. 없으면 평문 접속.
    ssl=os.environ.get("REDIS_SSL", "false").lower() == "true",
)
