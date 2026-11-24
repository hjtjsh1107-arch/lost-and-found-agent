from contextlib import contextmanager
from contextvars import ContextVar

calls = ContextVar("model_call_stats", default=None)
fixture_responses = ContextVar("non_sensitive_fixture_responses", default=None)


@contextmanager
def collect_calls():
    """개별 에이전트 실행에 속한 모델 호출 통계만 수집한 뒤 컨텍스트를 복원합니다."""
    entries = []
    token = calls.set(entries)
    try:
        yield entries
    finally:
        calls.reset(token)


@contextmanager
def inspect_fixture():
    """명시적으로 실행한 비민감 테스트 이미지의 모델 응답을 메모리에서 검사합니다."""
    entries = []
    token = fixture_responses.set(entries)
    try:
        yield entries
    finally:
        fixture_responses.reset(token)
