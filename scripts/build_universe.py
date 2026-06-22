"""FairValue 피어그룹 데이터 배치 수집기 (GitHub Actions 일일 실행용).

valuation_program/src/peer_group.py 의 가격/재무 수집 로직을 독립 스크립트로
복제한 것입니다. 이 스크립트는 별도 저장소(fairvalue-peer-data)에서 실행되므로
import 대신 복제했습니다 — peer_group.py의 수집 로직이 바뀌면 이 파일도 함께
갱신해야 합니다 (동기화 기준: 2026-06-22, FSC+DART 마이그레이션 완료 버전).

목적:
  고객 PC에 배포되는 FairValue EXE가 DART/FSC API 키를 직접 보유하지 않도록,
  개발자(본인) 소유 키로 하루 1회 전종목 PER/PBR/PSR/EV-EBITDA를 미리 계산해
  정적 JSON으로 GitHub에 publish한다. EXE는 이 JSON을 키 없이 HTTPS GET만
  하면 된다.

환경변수 (GitHub Secrets로 주입):
  DART_API_KEY      - dart_fss 라이브러리가 내부적으로 읽음
  FSC_DATA_API_KEY  - 금융위원회_주식시세정보 API 인증키

출력 (--out, 기본 data/peer_universe.json):
  {
    "generated_date": "YYYYMMDD",
    "generated_at_utc": "2026-06-22T07:00:00+00:00",
    "source": "FSC GetStockPriceInfo + DART extract_fs",
    "shard_index": null,
    "shard_count": null,
    "company_count": 2453,
    "companies": [
      {"ticker": "005930", "name": "삼성전자", "market": "KOSPI", "sector": "",
       "market_cap_bn": 12345.6, "per": 12.3, "pbr": 1.1, "psr": 0.9, "ev_ebitda": 6.2},
      ...
    ]
  }
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

_NAN = float("nan")
_FSC_PRICE_URL = "http://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return _NAN
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in {"", "-", "N/A", "nan"}:
                return _NAN
            if text.startswith("(") and text.endswith(")"):
                text = "-" + text[1:-1]
            value = text
        return float(value)
    except Exception:
        return _NAN


def _is_valid(value: float) -> bool:
    try:
        return not math.isnan(float(value))
    except Exception:
        return False


def _normalize_label(value: Any) -> str:
    return str(value).replace(" ", "").replace("_", "").lower()


def _value_to_okrw(value: Any) -> float:
    parsed = _as_float(value)
    if not _is_valid(parsed):
        return _NAN
    return parsed / 1e8


def _find_statement_value(df: pd.DataFrame, keys: list[str]) -> float:
    keys_n = [_normalize_label(k) for k in keys]

    def _matches(label: Any) -> bool:
        label_n = _normalize_label(label)
        return any(k in label_n for k in keys_n)

    try:
        for idx in df.index:
            if not _matches(idx):
                continue
            row = df.loc[idx]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            for value in reversed(list(row.values)):
                parsed = _value_to_okrw(value)
                if _is_valid(parsed):
                    return parsed

        label_cols = [c for c in df.columns if _matches(c)]
        for col in label_cols:
            for value in reversed(list(df[col].values)):
                parsed = _value_to_okrw(value)
                if _is_valid(parsed):
                    return parsed

        for _, row in df.iterrows():
            labels = " ".join(str(v) for v in row.values[:3])
            if not _matches(labels):
                continue
            for value in reversed(list(row.values)):
                parsed = _value_to_okrw(value)
                if _is_valid(parsed):
                    return parsed
    except Exception:
        return _NAN

    return _NAN


def _extract_financial_values_from_frames(frames: list[pd.DataFrame]) -> tuple[float, float, float, float, float, float, float]:
    revenue_keys = ["매출액", "매출", "영업수익", "revenue", "sales"]
    op_income_keys = ["영업이익", "operatingincome", "operatingprofit"]
    da_keys = ["감가상각", "상각비", "depreciation", "amortization", "da"]
    debt_keys = ["차입금", "단기차입금", "장기차입금", "borrowings", "debt"]
    cash_keys = ["현금및현금성자산", "현금", "cash"]
    net_income_keys = ["당기순이익", "분기순이익", "반기순이익", "netincome"]
    equity_keys = ["자본총계", "총자본", "equity", "totalequity"]

    revenue = op_income = da = total_debt = cash = net_income = equity = _NAN
    for df in frames:
        if df is None or df.empty:
            continue
        if not _is_valid(revenue):
            revenue = _find_statement_value(df, revenue_keys)
        if not _is_valid(op_income):
            op_income = _find_statement_value(df, op_income_keys)
        if not _is_valid(da):
            da = _find_statement_value(df, da_keys)
        if not _is_valid(total_debt):
            total_debt = _find_statement_value(df, debt_keys)
        if not _is_valid(cash):
            cash = _find_statement_value(df, cash_keys)
        if not _is_valid(net_income):
            net_income = _find_statement_value(df, net_income_keys)
        if not _is_valid(equity):
            equity = _find_statement_value(df, equity_keys)
    return revenue, op_income, da, total_debt, cash, net_income, equity


def _collect_dart_frames(obj: Any) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    if isinstance(obj, pd.DataFrame):
        frames.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            frames.extend(_collect_dart_frames(value))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            frames.extend(_collect_dart_frames(value))
    else:
        for attr in ("fs", "bs", "is_", "cis", "data"):
            try:
                value = getattr(obj, attr)
            except Exception:
                continue
            frames.extend(_collect_dart_frames(value))
    return frames


_dart_corp_list_cache: Any = None


def _get_dart_corp_list() -> Any:
    global _dart_corp_list_cache
    if _dart_corp_list_cache is None:
        import dart_fss as dart
        _dart_corp_list_cache = dart.get_corp_list()
    return _dart_corp_list_cache


def _try_get_dart_financial_data(date: str, ticker: str) -> tuple[float, float, float, float, float, float, float]:
    try:
        corp_list = _get_dart_corp_list()
        corp = corp_list.find_by_stock_code(ticker)
        if corp is None:
            return (_NAN,) * 7
        # progressbar=False: tqdm 진행바를 끈다. GitHub Actions 같은
        # 비대화형 로그 캡처 환경에서는 tqdm이 한 줄을 덮어쓰지 못하고
        # 매 갱신마다 새 로그 줄을 찍어 (300종목 기준 약 84,000줄) 멈춘 것처럼
        # 보이는 노이즈를 만든다. 처리 속도에는 영향 없음 (2026-06-23 확인,
        # dart_fss 공식 문서 기준 progressbar는 출력 전용 옵션).
        fs = corp.extract_fs(bgn_de=f"{date[:4]}0101", progressbar=False)
        frames = _collect_dart_frames(fs)
        return _extract_financial_values_from_frames(frames)
    except Exception:
        return (_NAN,) * 7


def _fetch_fsc_price_universe(date: str) -> pd.DataFrame:
    api_key = os.environ.get("FSC_DATA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FSC_DATA_API_KEY 환경변수가 설정되지 않았습니다 (GitHub Secrets 확인).")
    params = {
        "serviceKey": api_key, "pageNo": "1", "numOfRows": "3000",
        "resultType": "json", "basDt": date,
    }
    url = _FSC_PRICE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    body = payload["response"]["body"]
    header = payload["response"]["header"]
    result_code = str(header.get("resultCode", ""))
    if result_code != "00":
        raise RuntimeError(f"FSC API 오류: resultCode={result_code}, resultMsg={header.get('resultMsg', '')}")
    items = body.get("items", {})
    item_list = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item_list, dict):
        item_list = [item_list]
    rows = []
    for item in item_list:
        ticker = str(item.get("srtnCd", "")).strip()
        if not ticker:
            continue
        cap_val = _as_float(item.get("mrktTotAmt"))
        rows.append({
            "ticker": ticker,
            "name": str(item.get("itmsNm", "")).strip(),
            "market": str(item.get("mrktCtg", "")).strip(),
            "market_cap_bn": cap_val / 1e8 if _is_valid(cap_val) else _NAN,
            "price": _as_float(item.get("clpr")),
            "shares_outstanding": _as_float(item.get("lstgStCnt")),
        })
    return pd.DataFrame(rows)


def _fetch_sector_lookup(date: str, markets: list[str]) -> dict[str, str]:
    """pykrx 최후수단 - KRX가 스크래핑을 차단하면 예외 처리되어 빈 dict 반환.
    (2026-06-22 기준 KRX 403/400 차단 확인됨 - 정상적으로 빈 값 처리됨, 회귀 아님)"""
    sector_lookup: dict[str, str] = {}
    try:
        from pykrx import stock
        for mkt in markets:
            sector_df = stock.get_market_sector_classifications(date, mkt)
            sector_col = next((c for c in sector_df.columns if "업종" in str(c)), None)
            if sector_col:
                for ticker, sec_val in sector_df[sector_col].items():
                    sector_lookup[str(ticker)] = str(sec_val)
    except Exception:
        pass
    return sector_lookup


def _r(v: float) -> float | None:
    return None if not _is_valid(v) else round(v, 4)


def build_universe(
    date: str,
    limit: int | None = None,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> dict:
    markets = ["KOSPI", "KOSDAQ"]
    universe = _fetch_fsc_price_universe(date)
    if universe.empty:
        raise RuntimeError(f"FSC API가 {date} 기준 빈 유니버스를 반환했습니다 (휴장일/주말 가능성 확인).")
    universe = universe[universe["market"].isin(markets)].reset_index(drop=True)

    # pykrx 섹터조회는 비활성화함 (2026-06-22 GH Actions 테스트에서 KRX 차단으로
    # 무한 재시도(hang) 발생 확인 - 예외처리로 빈 값 반환된다는 기존 가정이 틀렸음).
    # PER/PBR/PSR/EV-EBITDA 핵심 지표에는 sector가 불필요하므로 완전히 제거.
    sector_lookup: dict[str, str] = {}

    candidates = universe.to_dict("records")

    # 병렬(matrix) 작업 분할: round-robin(매 N번째)으로 나눠서 한 샤드가
    # FSC 정렬상 우연히 "느린" 종목만 몰려받는 편향을 막는다
    # (2026-06-22 테스트에서 50종목 샘플=1.6s/종목, 300종목 샘플=10.4s/종목으로
    # 큰 차이가 난 것이 정렬 편향 때문이라는 가설에 대한 대응).
    if shard_count:
        if shard_index is None or shard_index < 0 or shard_index >= shard_count:
            raise ValueError(f"shard_index({shard_index})는 0~{shard_count - 1} 범위여야 합니다.")
        candidates = candidates[shard_index::shard_count]

    if limit:
        candidates = candidates[:limit]

    companies = []
    total_n = len(candidates)
    for i, row in enumerate(candidates, start=1):
        if i % 100 == 0 or i == total_n:
            print(f"[progress] {i}/{total_n}", flush=True)
        ticker = row["ticker"]
        try:
            (revenue, op_income, da, total_debt, cash, net_income, equity) = _try_get_dart_financial_data(date, ticker)
            cap_val = row["market_cap_bn"]
            price_val = row["price"]
            shares_val = row["shares_outstanding"]

            per_val = _NAN
            pbr_val = _NAN
            if _is_valid(net_income) and _is_valid(shares_val) and shares_val > 0:
                eps = (net_income * 1e8) / shares_val
                if eps > 0 and _is_valid(price_val):
                    per_val = price_val / eps
            if _is_valid(equity) and _is_valid(shares_val) and shares_val > 0:
                bps = (equity * 1e8) / shares_val
                if bps > 0 and _is_valid(price_val):
                    pbr_val = price_val / bps

            psr_val = _NAN
            ev_ebitda_val = _NAN
            if _is_valid(revenue) and revenue > 0:
                psr_val = cap_val / revenue
            da_n = 0.0 if not _is_valid(da) else da
            debt_n = 0.0 if not _is_valid(total_debt) else total_debt
            cash_n = 0.0 if not _is_valid(cash) else cash
            if _is_valid(op_income):
                ebitda = op_income + da_n
                if ebitda > 0:
                    ev_ebitda_val = (cap_val + debt_n - cash_n) / ebitda

            companies.append({
                "ticker": ticker, "name": row["name"], "market": row["market"],
                "sector": sector_lookup.get(ticker, ""),
                "market_cap_bn": _r(cap_val),
                "per": _r(per_val), "pbr": _r(pbr_val),
                "psr": _r(psr_val), "ev_ebitda": _r(ev_ebitda_val),
            })
        except Exception as exc:
            print(f"[warn] {ticker} 처리 실패: {exc}", file=sys.stderr)
            continue

    return {
        "generated_date": date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "FSC GetStockPriceInfo + DART extract_fs",
        "shard_index": shard_index,
        "shard_count": shard_count,
        "company_count": len(companies),
        "companies": companies,
    }


def _latest_business_date() -> str:
    """오늘이 주말이면 가장 최근 평일로 보정. (공휴일은 FSC가 빈 데이터로
    응답 -> build_universe()가 RuntimeError를 던져 워크플로우가 실패하고
    커밋하지 않음. 직전 데이터가 유지되므로 안전한 기본 동작.)"""
    d = datetime.now(timezone.utc) + timedelta(hours=9)  # KST 보정
    while d.weekday() >= 5:  # 5=토, 6=일
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def main():
    parser = argparse.ArgumentParser(description="FairValue 피어그룹 유니버스 배치 수집")
    parser.add_argument("--date", default=None, help="기준일 YYYYMMDD (기본: 최근 평일)")
    parser.add_argument("--out", default="data/peer_universe.json")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 종목 수 제한")
    parser.add_argument("--shard-index", type=int, default=None, help="병렬분할 인덱스 (0부터 시작, --shard-count와 함께 사용)")
    parser.add_argument("--shard-count", type=int, default=None, help="병렬분할 총 개수 (GitHub Actions matrix 작업용)")
    args = parser.parse_args()

    date = args.date or _latest_business_date()
    print(f"[info] 기준일: {date}, limit={args.limit}, shard={args.shard_index}/{args.shard_count}")
    result = build_universe(date, limit=args.limit, shard_index=args.shard_index, shard_count=args.shard_count)
    print(f"[info] 수집 완료: {result['company_count']}개 종목")

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[info] 저장 완료: {args.out}")


if __name__ == "__main__":
    main()
