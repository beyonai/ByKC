"""
并发压测脚本 — 验证异步改造后 DB/网络路径不阻塞 event loop。

用法：
    uv run python scripts/test_concurrent_async.py [--base-url http://127.0.0.1:8000] [--concurrency 20] [--rounds 3]

前提：服务已启动（uv run by-qa 或 uvicorn by_qa.main:app）
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

import httpx


async def create_kb(client: httpx.AsyncClient, base_url: str) -> tuple[float, int, str]:
    name = f"压测KB-{uuid.uuid4().hex[:6]}"
    t0 = time.perf_counter()
    resp = await client.post(
        f"{base_url}/api/v1/knowledgeBases/create",
        json={"knName": name},
        timeout=30,
    )
    elapsed = time.perf_counter() - t0
    body = resp.json()
    kb_code = body.get("resultObject", {}).get("knCode", "")
    return elapsed, resp.status_code, kb_code


async def list_dir(
    client: httpx.AsyncClient, base_url: str, kb_code: str
) -> tuple[float, int]:
    t0 = time.perf_counter()
    resp = await client.post(
        f"{base_url}/api/v1/listDir",
        json={"knCode": kb_code, "directoryPath": "/"},
        timeout=30,
    )
    elapsed = time.perf_counter() - t0
    return elapsed, resp.status_code


async def run_round(base_url: str, concurrency: int) -> dict:
    async with httpx.AsyncClient() as client:
        # 并发创建 KB
        t_start = time.perf_counter()
        create_tasks = [create_kb(client, base_url) for _ in range(concurrency)]
        create_results = await asyncio.gather(*create_tasks, return_exceptions=True)
        total_create = time.perf_counter() - t_start

        create_times = []
        kb_codes = []
        create_errors = 0
        for r in create_results:
            if isinstance(r, Exception):
                create_errors += 1
            else:
                elapsed, status, kb_code = r
                if status == 200 and kb_code:
                    create_times.append(elapsed)
                    kb_codes.append(kb_code)
                else:
                    create_errors += 1

        # 并发 listDir
        t_start = time.perf_counter()
        list_tasks = [list_dir(client, base_url, kc) for kc in kb_codes]
        list_results = await asyncio.gather(*list_tasks, return_exceptions=True)
        total_list = time.perf_counter() - t_start

        list_times = []
        list_errors = 0
        for r in list_results:
            if isinstance(r, Exception):
                list_errors += 1
            else:
                elapsed, status = r
                if status == 200:
                    list_times.append(elapsed)
                else:
                    list_errors += 1

    return {
        "concurrency": concurrency,
        "create": {
            "ok": len(create_times),
            "errors": create_errors,
            "wall_s": round(total_create, 3),
            "p50_ms": round(statistics.median(create_times) * 1000, 1)
            if create_times
            else None,
            "p99_ms": round(
                sorted(create_times)[int(len(create_times) * 0.99)] * 1000, 1
            )
            if len(create_times) >= 2
            else None,
            "max_ms": round(max(create_times) * 1000, 1) if create_times else None,
        },
        "list_dir": {
            "ok": len(list_times),
            "errors": list_errors,
            "wall_s": round(total_list, 3),
            "p50_ms": round(statistics.median(list_times) * 1000, 1)
            if list_times
            else None,
            "p99_ms": round(sorted(list_times)[int(len(list_times) * 0.99)] * 1000, 1)
            if len(list_times) >= 2
            else None,
            "max_ms": round(max(list_times) * 1000, 1) if list_times else None,
        },
    }


def print_result(r: dict, round_no: int) -> None:
    c = r["create"]
    ld = r["list_dir"]
    print(f"\n--- Round {round_no}  concurrency={r['concurrency']} ---")
    print(
        f"  createKB  ok={c['ok']} err={c['errors']}  wall={c['wall_s']}s  "
        f"p50={c['p50_ms']}ms  p99={c['p99_ms']}ms  max={c['max_ms']}ms"
    )
    print(
        f"  listDir   ok={ld['ok']} err={ld['errors']}  wall={ld['wall_s']}s  "
        f"p50={ld['p50_ms']}ms  p99={ld['p99_ms']}ms  max={ld['max_ms']}ms"
    )

    # 阻塞判断：如果 wall_time ≈ concurrency × p50，说明串行了
    if c["p50_ms"] and c["wall_s"]:
        serial_estimate = c["p50_ms"] * r["concurrency"] / 1000
        ratio = c["wall_s"] / serial_estimate
        if ratio > 0.7:
            print(
                f"  ⚠️  createKB wall={c['wall_s']}s ≈ serial estimate {serial_estimate:.1f}s (ratio={ratio:.2f}) — 可能存在阻塞"
            )
        else:
            print(
                f"  ✅ createKB 并发正常 wall={c['wall_s']}s << serial estimate {serial_estimate:.1f}s (ratio={ratio:.2f})"
            )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    # 健康检查
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{args.base_url}/health", timeout=5)
            print(f"服务健康: {resp.text}")
        except Exception as e:
            print(f"服务未响应: {e}\n请先启动服务: uv run by-qa")
            return

    print(f"\n并发={args.concurrency}  轮次={args.rounds}  目标={args.base_url}")
    for i in range(1, args.rounds + 1):
        result = await run_round(args.base_url, args.concurrency)
        print_result(result, i)


if __name__ == "__main__":
    asyncio.run(main())
