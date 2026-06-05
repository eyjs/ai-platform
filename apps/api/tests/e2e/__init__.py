"""3-서비스 골든패스 E2E + 실패주입 하니스 (P3 Step 17).

KMS(:3001) → ai-platform(:8020) → docforge(:5051) seam을 잇는 라이브 E2E.
이 패키지의 모든 테스트는 AIP_E2E_LIVE=1 게이트로 보호된다.
미설정 시 전체 skip + 사유 (조용한 통과 금지).
"""
