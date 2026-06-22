# fairvalue-peer-data

FairValue Pro 피어그룹(PER/PBR/PSR/EV-EBITDA) 데이터를 매일 1회 자동 수집해
정적 JSON으로 공개하는 배치 파이프라인입니다.

**목적**: 배포되는 FairValue EXE가 고객 PC에서 DART/FSC API 키 없이도
피어그룹 조회 기능을 쓸 수 있도록, 개발자(본인) 소유 키로 하루 1회 전종목을
미리 계산해 `data/peer_universe.json`으로 publish합니다. 고객은 키 발급/설정을
전혀 할 필요가 없습니다.

## 설정 체크리스트 (최초 1회, 본인만 수행)

1. **GitHub에서 새 저장소 생성** — Public으로 생성해야 합니다 (raw.githubusercontent.com이
   비공개 저장소는 인증 없이 접근 불가). 저장소 이름 예: `fairvalue-peer-data`
   (이 데이터는 공개 시세/재무 정보 가공물이라 공개해도 무방합니다).

2. **이 폴더 전체를 새 저장소에 업로드**
   - git을 쓴다면:
     ```
     cd fairvalue-peer-data
     git init
     git add .
     git commit -m "init"
     git branch -M main
     git remote add origin https://github.com/<본인계정>/fairvalue-peer-data.git
     git push -u origin main
     ```
   - git이 익숙하지 않다면: github.com 저장소 페이지 → "Add file" → "Upload files"
     → 이 폴더의 모든 파일/하위폴더를 드래그앤드롭 (가장 쉬운 방법).

3. **API 키를 GitHub Secrets로 등록** — 저장소 페이지 →
   Settings → Secrets and variables → Actions → "New repository secret"
   - `DART_API_KEY` : valuation_program/.env 파일에 있는 값과 동일하게 입력
   - `FSC_DATA_API_KEY` : 동일 파일의 값과 동일하게 입력
   - (키 값은 본인만 입력하는 것이며, 어디에도 공유되지 않습니다.)

4. **수동으로 1회 실행해 정상 동작 확인** — 저장소 페이지 → Actions 탭 →
   "Update Peer Universe Data" → "Run workflow" 버튼 클릭.
   몇 분 후 `data/peer_universe.json` 파일이 생성되면 성공입니다.

5. **완료되면 알려주세요** — 다음 raw URL이 살아있는지 확인 후 전달해 주시면
   `src/peer_group.py`가 이 주소를 호출하도록 반영하겠습니다:
   ```
   https://raw.githubusercontent.com/<본인계정>/fairvalue-peer-data/main/data/peer_universe.json
   ```

이후로는 평일 16:00 KST(장 마감 후)에 자동으로 갱신됩니다. 공휴일처럼 FSC가
빈 데이터를 반환하는 날은 워크플로우가 실패하고 커밋하지 않아, 직전 데이터가
그대로 유지됩니다 (안전한 기본 동작).

## 파일 구조

```
fairvalue-peer-data/
├── .github/workflows/update_peer_data.yml   # 매일 16:00 KST 자동 실행
├── scripts/build_universe.py                # 수집/계산 스크립트 (단독 실행 가능)
├── requirements.txt
├── data/peer_universe.json                  # 워크플로우가 생성 (최초엔 없음)
└── README.md
```

## 로컬 테스트

```
pip install -r requirements.txt
set DART_API_KEY=...        (또는 export, OS에 맞게)
set FSC_DATA_API_KEY=...
python scripts/build_universe.py --limit 10 --out data/peer_universe.json
```

## 유지보수 메모

`scripts/build_universe.py`의 수집 로직은 `valuation_program/src/peer_group.py`의
`_fetch_fsc_price_universe` / `_try_get_dart_financial_data` 등을 그대로 복제한
것입니다 (별도 저장소라 import 불가). peer_group.py의 해당 로직이 바뀌면 이
파일도 함께 갱신해야 합니다. 동기화 기준일: 2026-06-22.
