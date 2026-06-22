"""matrix 병렬작업으로 생성된 peer_universe_shard_N.json 파일들을 하나로 합친다.

GitHub Actions의 update_peer_data.yml에서 build-shard 잡(matrix)이 각각
data/peer_universe_shard_{N}.json 을 만들고 artifact로 업로드한 뒤,
이 스크립트가 merge 잡에서 모든 artifact를 내려받아 합쳐 최종
data/peer_universe.json 으로 저장하고 커밋한다.

ticker 중복 제거: round-robin 분할(shard_index::shard_count)이라 정상
실행에서는 중복이 생기지 않지만, 재실행/부분실패 등 예외상황에 대한
방어적 처리로 ticker 기준 dedup을 둔다 (먼저 나온 파일의 값을 우선).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone


def merge_shards(shard_glob: str) -> dict:
    files = sorted(glob.glob(shard_glob, recursive=True))
    if not files:
        raise RuntimeError(f"샤드 파일을 찾을 수 없습니다: {shard_glob}")

    all_companies: list[dict] = []
    seen_tickers: set[str] = set()
    generated_date = None
    shard_count_declared = None
    shard_indices_seen: set[int] = set()

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)

        if generated_date is None:
            generated_date = data.get("generated_date")
        elif data.get("generated_date") != generated_date:
            print(f"[warn] {fp}의 generated_date={data.get('generated_date')!r}가 "
                  f"앞선 샤드({generated_date!r})와 다릅니다 - 같은 날 실행인지 확인 필요.")

        sc = data.get("shard_count")
        if sc is not None:
            shard_count_declared = sc
        si = data.get("shard_index")
        if si is not None:
            shard_indices_seen.add(si)

        for company in data.get("companies", []):
            ticker = company.get("ticker")
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            all_companies.append(company)

        print(f"[info] {fp}: {data.get('company_count', len(data.get('companies', [])))}개 종목 로드")

    if shard_count_declared is not None:
        expected = set(range(shard_count_declared))
        missing = expected - shard_indices_seen
        if missing:
            print(f"[warn] shard_count={shard_count_declared}인데 누락된 shard_index: {sorted(missing)} "
                  f"- 일부 matrix 잡이 실패했을 가능성이 있습니다.")

    return {
        "generated_date": generated_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "FSC GetStockPriceInfo + DART extract_fs (merged from shards)",
        "shard_files_merged": len(files),
        "company_count": len(all_companies),
        "companies": all_companies,
    }


def main():
    parser = argparse.ArgumentParser(description="피어 유니버스 샤드 병합")
    parser.add_argument("--shard-glob", default="shard-artifacts/**/peer_universe_shard_*.json",
                         help="병합할 샤드 JSON glob 패턴")
    parser.add_argument("--out", default="data/peer_universe.json")
    args = parser.parse_args()

    result = merge_shards(args.shard_glob)
    print(f"[info] 병합 완료: {result['shard_files_merged']}개 샤드 파일 -> {result['company_count']}개 종목")

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[info] 저장 완료: {args.out}")


if __name__ == "__main__":
    main()
