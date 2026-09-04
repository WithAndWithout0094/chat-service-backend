"""Redis 접근을 감싸는 함수 세 개.

Redis 명령을 그대로 쓰면 Redis 장애가 곧 API 500 이 된다.
여기서 RedisError 를 잡아 "캐시 없음"처럼 처리하므로, Redis 가 죽어도 API 는
느려질 뿐 계속 응답한다.

무효화 원칙: 데이터를 바꾸는 코드가 그 데이터를 담은 캐시 키를 직접 지운다.
TTL 은 무효화를 빠뜨린 경로를 위한 안전망이다.
"""

import logging

from redis.exceptions import RedisError   # 연결 실패·타임아웃이 모두 이 예외의 자식이다

from app.redis_client import r

logger = logging.getLogger(__name__)


def cache_get(key: str) -> str | None:
    """캐시에서 값을 읽는다. 없거나 Redis 가 죽었으면 None.

    저장은 항상 문자열이므로, dict/list 를 넣은 쪽은 json.loads 로 되돌려 쓴다.
    """
    try:
        return r.get(key)
    except RedisError as error:
        logger.warning("캐시 조회 실패 (%s): %s", key, error)
        return None      # 캐시 미스로 위장한다 → 호출한 쪽은 DB 로 간다


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    """캐시에 값을 쓴다. 실패해도 응답 흐름을 막지 않는다."""
    try:
        r.set(key, value, ex=ttl_seconds)
    except RedisError as error:
        logger.warning("캐시 저장 실패 (%s): %s", key, error)


def cache_delete(key: str) -> None:
    """캐시를 지운다(무효화). 없는 키를 지워도 오류가 아니다."""
    try:
        r.delete(key)
    except RedisError as error:
        logger.warning("캐시 삭제 실패 (%s): %s", key, error)
