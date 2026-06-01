"""프로필 인가 백필 — AIP_PROFILE_AUTH_STRICT 플립 전 무중단 전환용 (A1, Step 1).

배경:
  strict=False(현재)에서는 빈 allowed_profiles / 빈 테넌트 매핑 = 전체 허용(fail-open).
  strict=True로 전환하면 그 의미가 "전체 거부"로 뒤집힌다. 그대로 켜면 지금 fail-open에
  의존하던 키/테넌트가 즉시 차단된다.

이 스크립트는 플립 *전에* 현재 동작을 명시적으로 고정한다:
  1) api_keys: allowed_profiles가 비어(NULL/{}) 있으면 ['*'] (명시적 전체 허용)로 설정.
     - allowed_profiles는 TEXT[]이고 FK가 없어 와일드카드 토큰을 그대로 쓸 수 있다.
  2) tenants: tenant_profiles 매핑이 0건인 테넌트는 *현재 존재하는 모든 프로필*을
     명시적으로 구체화(materialize)한다.
     - tenant_profiles.profile_id는 agent_profiles에 FK이므로 '*' 토큰을 넣을 수 없다.
       따라서 전체 프로필 행을 삽입해 "전체 허용"을 FK-안전하게 보존한다.
     - 이후 추가되는 신규 프로필은 자동 적용되지 않는다(= 의도된 더 엄격한 기본값).

사용:
  python -m scripts.backfill_profile_authz            # dry-run (변경 없음, 리포트만)
  python -m scripts.backfill_profile_authz --apply    # 실제 적용 (단일 트랜잭션)

멱등성: 재실행해도 안전하다(이미 채워진 행은 건너뜀).
"""

from __future__ import annotations

import argparse
import asyncio

import asyncpg

from src.config import settings


async def _report(conn: asyncpg.Connection) -> dict:
    empty_keys = await conn.fetchval(
        "SELECT count(*) FROM api_keys "
        "WHERE allowed_profiles IS NULL OR cardinality(allowed_profiles) = 0"
    )
    profile_ids = [r["id"] for r in await conn.fetch("SELECT id FROM agent_profiles")]
    tenants_no_map = [
        r["id"]
        for r in await conn.fetch(
            "SELECT t.id FROM tenants t "
            "WHERE NOT EXISTS (SELECT 1 FROM tenant_profiles tp WHERE tp.tenant_id = t.id)"
        )
    ]
    return {
        "empty_api_keys": empty_keys,
        "profile_count": len(profile_ids),
        "profile_ids": profile_ids,
        "tenants_without_mapping": tenants_no_map,
    }


async def run(apply: bool) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        snap = await _report(conn)
        print("── 백필 대상 ──")
        print(f"  빈 allowed_profiles api_keys : {snap['empty_api_keys']}건 → ['*']로 설정")
        print(f"  전체 프로필 수               : {snap['profile_count']}")
        print(
            f"  매핑 없는 테넌트             : {len(snap['tenants_without_mapping'])}개 "
            f"→ 각 테넌트에 {snap['profile_count']}개 프로필 구체화"
        )

        if not snap["profile_ids"] and snap["tenants_without_mapping"]:
            print(
                "  ⚠️  agent_profiles가 비어있어 테넌트 매핑을 구체화할 수 없습니다. "
                "프로필 시드 후 다시 실행하세요."
            )

        if not apply:
            print("\n[dry-run] 변경 없음. 적용하려면 --apply")
            return

        async with conn.transaction():
            updated_keys = await conn.execute(
                "UPDATE api_keys SET allowed_profiles = ARRAY['*'] "
                "WHERE allowed_profiles IS NULL OR cardinality(allowed_profiles) = 0"
            )
            inserted = 0
            for tenant_id in snap["tenants_without_mapping"]:
                for pid in snap["profile_ids"]:
                    await conn.execute(
                        "INSERT INTO tenant_profiles (tenant_id, profile_id) "
                        "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        tenant_id, pid,
                    )
                    inserted += 1

        print(f"\n✅ 적용 완료: api_keys {updated_keys}, tenant_profiles 삽입 {inserted}행")
        print("   이제 AIP_PROFILE_AUTH_STRICT=true 로 무중단 전환 가능합니다.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="프로필 인가 백필 (strict 플립 전 실행)")
    parser.add_argument("--apply", action="store_true", help="실제 적용 (기본: dry-run)")
    args = parser.parse_args()
    asyncio.run(run(args.apply))
