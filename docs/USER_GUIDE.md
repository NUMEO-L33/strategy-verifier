# 검증기 사용자 가이드

이 문서 하나로 처음 온 사람이 접수부터 판정까지 한 사이클을 완주하는
것이 목표다. 명령은 전부 복사해 실행할 수 있다.

## 0. 이 프로그램이 하는 것 / 하지 않는 것

하는 것: 매매 기법의 주장("이 조건이면 이 방향")을 과거 실데이터
위에서 판정한다. 조건 성립 순간마다 가격 경주를 붙이고, 승률을
동전(50%) 또는 대조군과 통계적으로 비교하며, 탐색 시대에서 고른 것을
격리된 확인 시대에서 재판한다. 결과는 판정문(통과 / NOT_CONFIRMED),
진단서(어느 구간이 좋고 어느 구간이 문제인지 등급), 등식
스탬프(시장+기법+주문=결과의 해시)다.

하지 않는 것: 매매 신호 생성, 투자 자문, 수익 보장, 파라미터 튜닝
권고. 매매 주문 코드는 이 저장소에 존재하지 않는다. 모든 출력은 연구
전용이다.

용어 하나만 먼저: family(가족)는 검증의 기본 단위다. 기법 하나의
등록 이름이자 그 파라미터 변형 전체(그리드 셀들)의 묶음이며,
판정은 잘 나온 변형 하나가 아니라 가족 전체에 내려진다. 한 번
등록한 가족 이름은 재사용할 수 없다.

## 1. 설치

두 가지 경로가 있다. 구매자는 A(배포판), 개발자는 B(저장소)를 쓴다.

### 1-A. 배포판(wheel)으로

```
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python3 -m pip install strategy_verifier-1.0.0-py3-none-any.whl
verifier --version                   # verifier 1.0.0
verifier init --dir myverifier       # 작업 골격 전개 (기존 파일 보존)
cd myverifier
verifier verify-status --all         # "no registered families" 면 준비 완료
```

이후 이 가이드의 2장부터 그대로 따른다. 브라우저 화면을 원하면
`verifier ui --dir myverifier` — 현황판과 클레임 현관이 열린다
(127.0.0.1 전용, 파이프라인 나머지 화면은 다음 판에서 이어진다). 데모 정의·이 가이드·면책·
헌장·라이선스가 작업 폴더에 이미 있다.

### 1-B. 저장소로 (개발)

요구: Python 3.11 이상, git. `pip` 단독 명령은 환경에 없을 수 있으므로
아래처럼 항상 `python3 -m pip` 형태를 쓰고, 가상환경을 먼저 만든다.

```
git clone <repo-url> && cd <repo>
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python3 -m pip install -e ".[dev]"   # dev = 검증용 pytest 포함
python3 -m pytest -q             # 전부 통과해야 정상
verifier verify-status --all     # "no registered families" 면 준비 완료
```

이후 모든 명령은 이 가상환경이 활성화된 셸에서 실행한다(새 터미널을
열면 `source .venv/bin/activate` 부터). `verifier <명령>`과
`python3 -m strategy_verifier <명령>`은 동일하다.

## 2. 핵심 개념 3개

1) 등식: 시장(데이터 해시) + 기법(정의 파일 해시) + 주문(명령) =
결과(리포트 해시). 모든 리포트 끝의 `equation:` 블록이 이를 찍는다.
같은 입력은 같은 결과를 바이트까지 재현하고, 결과 해시가 다르면
입력이 달랐다는 뜻이다.

2) 2단계: 탐색 창에서 보고 고르되, 판정은 손대지 않은 확인 창이
내린다. 두 창은 달력상 겹칠 수 없고(등록이 거부한다), 확인 창
데이터는 동결 전에 내려받지 않는다.

3) 판정과 진단의 분리: 판정문은 등록된 기준의 통과 여부 한 줄이고,
진단서는 전 구간 등급표다. 진단은 처방하지 않는다.

## 3. 완주 예제 — 기법 하나를 처음부터 끝까지

예제 기법: "상승 추세에서 종가가 20이평에 닿으면 롱, 미러면 숏"
(저장소의 `definitions/ma_touch_trend.toml`).

### 3.1 클레임 작성과 현관 통과

`claims/my_claim.toml`:

```toml
[claim]
family = "ma_touch_trend"
direction = "종가가 200이평 위이고 200이평이 12봉 전보다 높으면 롱, 미러면 숏"
data = ["klines"]
conditions = [
  "종가와 20이평의 정규화 거리가 delta 이하",
]
```

```
verifier verify-triage --claim claims/my_claim.toml
```

exit 0이면 입장. exit 2면 반려 — 리포트가 사유 코드와 고침법을
나열한다. 흔한 반려: direction에 "느낌", "적절히" 같은 재량어(코드
HIDDEN_DISCRETION), 없는 데이터 참조(UNAVAILABLE_DATA; 가용:
trades, klines, daily_klines, funding, open_interest).

### 3.2 정의 파일 — 기법을 기계어로

엔진은 graph_v1 하나다. 어떤 조립이든 파일로 쓴다. 기반값(close, volume, open,
high, low, body, body_abs, upper_wick, lower_wick, range) →
시리즈(sma, smma, rolling_max/min/median — feed="delayed" 지원,
mean_abs_return, lag, distance, pair_max, pair_min, ratio,
cmf — 차이킨 자금흐름, period 지정, 봉의 고·저·종·거래량 직접 사용) →
조건(compare, ratio_compare, crosses, run, order_block — 캔들+변위
불리시 오더블록 재진입, push_window·displacement 지정 + 방향 확정 후
평가되는 signed_compare, side_gate) → 방향 규칙(또는
direction.from_pattern) →
이벤트+게이트(또는 event.pattern: head_shoulders 패턴 노드가 자체
게이트·5극점 자격심사·데드라인·거래량 분할까지 수행) → 스트라이드
대조군(선택; blocked_by로 차단 조건 지정 가능) → [[splits]] 축별
분할 grid. `definitions/ma_touch_trend.toml`(이평 터치)과
`definitions/wick_reversal.toml`(캔들 꼬리 반전)이 완전한 견본이다.
참조는 뒤로만 가능하고, 미지 키는 전부 거부되며, 방향 이후
조건(signed/side)은 방향 규칙 안에서 쓸 수 없다.

캔들 해부(open/high/low와 꼬리·몸통)는 틱 데이터에서는 자동으로
정확하고, 캔들 데이터에서는 변환 시 `--bar-path ohlc` 를 주면 봉당
시가·고가·저가·종가 4포인트로 보존된다(기존 close 모드는 그대로).

### 3.3 데이터 준비 (예: 15분봉, 탐색 창 3개월)

```
mkdir -p data/captures
for SYM in BTCUSDT ETHUSDT SOLUSDT; do
  sym=$(echo $SYM | tr A-Z a-z)
  for YM in 2026-01 2026-02 2026-03; do
    curl -sO "https://data.binance.vision/data/futures/um/monthly/klines/${SYM}/15m/${SYM}-15m-${YM}.zip"
    verifier convert-binance-um-klines \
      --input ${SYM}-15m-${YM}.zip \
      --output data/captures/${sym}-15m-${YM}-100-1.jsonl \
      --symbol $SYM && rm ${SYM}-15m-${YM}.zip
  done
done
```

주의: 여기서 받는 것은 탐색 창뿐이다. 확인 창은 동결 단계 전까지
내려받지 않는다.

### 3.4 공변량 캘리브레이션 — delta를 데이터가 정하게

정의의 `[calibrate] series = "touch_gap"` 이 사다리 대상이다.

```
verifier verify-run \
  --definition definitions/ma_touch_trend.toml \
  --input data/captures/btcusdt-15m-*-100-1.jsonl \
  --input-session-mode single --integrity-gated \
  --calibrate-only
```

출력의 `calibrate_percentiles:` 에서 원하는 분위(p10, p25 등)를 읽어
정의의 delta에 적는다. 캘리브레이션은 결과(경주)를 계산하지 않으므로
등록 전에 수행해도 규율 위반이 아니다. 3마켓을 합산하려면 각 값을
읽고 표본 가중으로 고른다.

### 3.5 등록 — 데이터가 판정에 닿기 전에 박제

`claims/my_registration.toml`:

```toml
[claim]
family = "ma_touch_trend"
direction = "종가가 200이평 위이고 200이평이 12봉 전보다 높으면 롱, 미러면 숏"
data = ["klines"]
conditions = ["종가와 20이평의 정규화 거리가 delta 이하"]

[registration]
engine = "graph_v1"
definition = "definitions/ma_touch_trend.toml"
explore = "2026-01-01..2026-03-31"
confirm = "2024-01-01..2024-06-30"
```

```
verifier verify-register --file claims/my_registration.toml
```

산출 3개: 등록 문서(`docs/MA_TOUCH_TREND_REGISTRATION.md`, 정의 파일
전체 sha256 포함), 생애 상태(`state/ma_touch_trend.toml`,
stage=registered), 리포트의 해시들. 창이 겹치면 거부, 같은 가족명
재등록도 거부된다(새 시선엔 새 이름).

### 3.6 탐색 측정

```
for sym in btcusdt ethusdt solusdt; do
  verifier verify-run \
    --definition definitions/ma_touch_trend.toml \
    --input $(ls data/captures/${sym}-15m-*-100-1.jsonl | sort) \
    --input-session-mode single --integrity-gated \
    > reports/${sym}_mtt_explore.txt
done
verifier verify-advance --family ma_touch_trend --to calibrated
verifier verify-advance --family ma_touch_trend --to explored
```

### 3.7 진단서 읽기

```
verifier verify-diagnose --family ma_touch_trend \
  --report reports/btcusdt_mtt_explore.txt \
           reports/ethusdt_mtt_explore.txt \
           reports/solusdt_mtt_explore.txt \
  --primary-horizon 1000000 --primary-threshold 0.01 \
  --arm base_grid --control-arm control_grid \
  --side-arms "side=long,side=short"
```

등급표: EDGE(Wilson 하한>0.5) / INVERSE(상한<0.5) / LEAN±(유의성 없는
기울기) / FLAT / THIN(표본<100). `cell_grades`가 지평×임계 전 구간의
판정, `problems`가 구조적 문제(지평 감쇠, 단일 마켓 의존, 대조군
동행, 표본 기아, 사이드 비대칭, 소음 내 분할)의 증거와 함의,
`edges_present`가 유의한 강점 구간의 사실 목록이다.

### 3.8 동결 또는 종결

EDGE가 표본 100 이상으로 존재하고 마켓이 동의하면:

```
verifier verify-advance --family ma_touch_trend --to frozen \
  --note "frozen: base_grid at 1000000/0.01; ..."
```

이제, 그리고 이제서야, 확인 창 데이터를 내려받아 3.6과 동일하게
측정한다(파일명만 confirm으로). 아무 구간도 서지 않으면:

```
verifier verify-advance --family ma_touch_trend --to closed \
  --note "closed at exploration: every cell at or below the coin"
```

종결은 영구다. 같은 가족은 다시 열 수 없다.

### 3.9 판정

```
verifier verify-verdict --family ma_touch_trend \
  --report reports/btcusdt_mtt_confirm.txt \
           reports/ethusdt_mtt_confirm.txt \
           reports/solusdt_mtt_confirm.txt \
  --primary-horizon 1000000 --primary-threshold 0.01 \
  --arm base_grid --control-arm control_grid
```

exit 0 = 통과(`..._CONFIRMED`), exit 2 = `..._NOT_CONFIRMED`. 이후
`verify-advance --to confirmed|closed` 에 판정문을 메모로 남긴다.

### 3.10 상태 판독 — 검증된 채로 읽기

판정이 끝난(또는 진행 중인) graph 정의를 최신 데이터에 적용해, 규칙의
성립 지점들과 각 지점의 과거 결과, 최신 봉의 조건 상태를 본다:

```
verifier verify-read \
  --definition definitions/ma_touch_trend.toml \
  --input data/captures/btcusdt-15m-2026-0*.jsonl \
  --input-session-mode single --integrity-gated --last-bars 3
```

출력에는 가족의 판정 상태가 항상 함께 표시된다 — 종결됐다면 그
판정문이, 판정 전이라면 UNADJUDICATED 배너가. 이 리포트는 현상의
서술이며, 무엇을 하라는 지시가 아니다. 행동 여부는 전적으로 읽는
사람의 판단이다.

### 3.11 감정서 — 판정을 한 장으로

판정문과 진단서를 A4 인쇄용 HTML 한 장으로 접는다. 어떤 브라우저에서
열어 인쇄하면 PDF 로 저장된다:

```
verifier verify-verdict ... > reports/my_verdict.txt
verifier verify-diagnose ... > reports/my_diagnosis.txt
verifier verify-certificate --family my_technique \
  --verdict-report reports/my_verdict.txt \
  --diagnosis-report reports/my_diagnosis.txt \
  --output reports/my_certificate.html
```

감정서의 모든 수치는 인용된 리포트에 존재하고, 등식 해시가 그
파일들을 특정한다. 면책 문안이 문서 하단에 고정 인쇄된다.

## 4. 참조표

명령 종료코드: 0 성공/통과 · 2 반려/미확인 · 1 오류.

생애 단계: registered → (calibrated) → explored → frozen →
confirmed | closed. 순서 위반은 허용 다음 수를 알려주며 거부되고,
frozen·confirmed·closed 는 메모가 필수다. closed 는 영구다.

반려 코드: MISSING_FAMILY / MISSING_DIRECTION / HIDDEN_DISCRETION /
MISSING_DATA / UNAVAILABLE_DATA / MISSING_CONDITIONS.

## 5. 경계

연구 전용 도구다. 어떤 출력도 매매 지시가 아니고, 어떤 판정도 미래
수익을 보장하지 않는다. NOT_CONFIRMED 는 "거짓 증명"이 아니라 "이
기준·이 표본에서 미확인"이며, CONFIRMED 는 "과거 창에서 등록 기준을
넘었음"이지 그 이상이 아니다. 이 도구는 확신을 팔지 않는다. 확신의
검증 절차를 판다.

전체 면책·고지 문안은 [docs/DISCLAIMER.md](DISCLAIMER.md), 사용권
조건은 저장소 루트의 LICENSE 에 있으며, 이 가이드를 따라 도구를
사용하는 것은 두 문서에 대한 동의를 전제한다.
