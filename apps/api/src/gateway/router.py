"""AI Gateway: FastAPI 엔드포인트 (얇은 facade).

Step22 G25: 1327줄 god-file을 `src/gateway/routes/*`로 순수 이동 분할했다.
라우트 경로·HTTP 메서드·요청/응답·공개 import는 전부 불변이다.

기존 import 경로(`from src.gateway.router import gateway_router, APP_VERSION,
wait_for_pending_requests`)를 그대로 유지하기 위한 재수출 facade.
실제 구현은 `src/gateway/routes/` 패키지에 있다.

원래 엔드포인트 그룹:
- /health, /profiles            -> routes/public.py   (공개)
- /chat, /chat/stream           -> routes/chat.py
- /documents/ingest, /chat/sessions/{id}/files, /documents/ingest/{job_id}
                                -> routes/ingest.py
- /workflows, /workflow/start, /workflow/advance
                                -> routes/workflow.py
- /api-keys                     -> routes/admin.py
- /feedback, /admin/feedback    -> routes/feedback.py
- /sessions, /sessions/{id}/history
                                -> routes/session.py
공용 로직(인증/레이트리밋/세션 세팅/카운터)은 routes/helpers.py.
"""

from src.gateway.routes import gateway_router
from src.gateway.routes.helpers import APP_VERSION, wait_for_pending_requests

__all__ = ["gateway_router", "APP_VERSION", "wait_for_pending_requests"]
