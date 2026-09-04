"""Gemini 모델 접속과 시스템 프롬프트 조립.

말투·길이 선택지(TONES / LENGTHS)를 이 파일 한 곳에서만 관리한다.
화면은 목록을 하드코딩하지 않고 ``GET /chat/options`` 로 받아 간다.
그래야 선택지를 늘려도 화면과 프롬프트가 어긋나지 않는다.
"""

import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

# 실제 접속은 generate_content 를 부를 때 일어난다.
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# 모델명은 Google 이 수시로 바꾼다. models.list() 에 이름이 남아 있어도 실제 호출은
# 404 가 날 수 있으므로, 바꿀 때는 반드시 실제로 호출해 확인한다.
GEMINI_MODEL = "gemini-3.5-flash-lite"

# 사용자가 바꿀 수 없는 고정 지시.
BASE_PROMPT = (
    "당신은 채용 면접관입니다. 지원자가 면접을 연습할 수 있도록 돕습니다. "
    "지원자의 답변을 듣고 짧게 평가한 뒤, 이어지는 면접 질문을 하나 던지세요. "
    "지원자가 실제 이름이나 연락처를 말하면 그 정보를 되풀이하지 말고 넘어가세요."
)

# { 화면에 보이는 라벨: 모델에게 실제로 가는 문장 }
TONES = {
    "깐깐하게": "지원자의 답변에서 근거가 약한 부분을 날카롭게 짚습니다.",
    "친절하게": "지원자가 편하게 말할 수 있도록 격려하며 반응합니다.",
}

LENGTHS = {
    "짧게": "3문장 이내로 답하세요.",
    "보통": "5문장 내외로 답하세요.",
    "자세히": "예시를 들어 설명하되 10문장을 넘기지 마세요.",
}

DEFAULT_TONE = "친절하게"
DEFAULT_LENGTH = "보통"


def build_system_prompt(job_title: str, tone: str | None, length: str | None) -> str:
    """직무와 사용자 선택값으로 시스템 프롬프트 한 문장을 조립한다.

    tone/length 가 None 이거나 표에 없는 값이면 기본값 문장으로 대체한다.
    화면이 어떤 값을 보내도 서버가 답을 만들 수 있게 하려는 폴백이다.
    """
    return " ".join(
        [
            BASE_PROMPT,
            f"지원 직무는 '{job_title}' 입니다.",
            TONES.get(tone, TONES[DEFAULT_TONE]),
            LENGTHS.get(length, LENGTHS[DEFAULT_LENGTH]),
        ]
    )
