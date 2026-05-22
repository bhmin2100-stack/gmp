# GMP 근무표 자동 생성기

교대 근무표를 월 단위로 자동 생성하고, 관리자가 엑셀처럼 직접 수정할 수 있는 Windows용 데스크톱 앱입니다.

## 주요 기능

- 월별 근무표 자동 생성
- 평일 `Day / Swing / GY` 공평 분배
- 휴일·주말 `Day / Swing / GY` 별도 공평 분배
- 대한민국 공휴일 자동 반영 (`holidays` 패키지 사용)
- 개인 불가일 절대 배정 제외
- 신규 인원 교육 목적 순환 배치
  - 평일 Day 또는 Swing
  - 휴일 Day 또는 Swing
  - 평일 GY
- 토요일 `토당` 자동 배정 옵션
- 엑셀처럼 셀 직접 수정 / 붙여넣기 가능
- 실시간 검증
  - 최소 인원 부족
  - 불가일 배정 위반
  - 연속 근무 초과
  - 연속 GY 초과
- 사람별 통계와 평균 대비 편차 표시
- 엑셀 저장: 근무표 / 통계 / 검증 시트 포함

## 화면 구조

1. **월간 근무표**
   - 가로축: 월별 날짜와 요일
   - 세로축: 성명, 사번, 일자별 근무
   - 셀에 `Day`, `Swing`, `GY`, `토당` 직접 입력 가능

2. **직원 관리**
   - 엑셀에서 `성명 | 사번 | 신규 | 불가일` 형태로 붙여넣기
   - 불가일 예시: `2026-05-03, 2026-05-14` 또는 선택 월 기준 `3, 14`

3. **근무 설정**
   - 평일/휴일별 최소 인원 설정
   - 토당 자동 배정 여부
   - 최대 연속 근무 / 연속 GY 기준 설정

4. **통계/공정성**
   - Day, Swing, GY, 휴일 근무, 토당, 휴무, 최대 연속근무
   - 평균 대비 편차 표시

## 설치 및 실행

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
python main.py
```

macOS/Linux에서 테스트할 때는 다음처럼 실행할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Windows 실행 파일 만들기

Windows PC에서 아래 명령을 실행하면 단일 exe 파일을 만들 수 있습니다.

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name GMP-Scheduler main.py
```

생성 위치:

```text
dist/GMP-Scheduler.exe
```

## 직원 엑셀 import 형식

첫 번째 시트에 아래 컬럼 중 일부 또는 전부가 있으면 됩니다.

| 성명 | 사번 | 신규 | 불가일 |
|---|---|---|---|
| 김민준 | 1001 | N | 2026-05-03, 2026-05-17 |
| 박도윤 | 1003 | Y | 9, 20 |

인식 가능한 컬럼명:

- 성명: `성명`, `이름`, `직원명`, `name`
- 사번: `사번`, `직번`, `번호`, `id`, `employee_id`
- 신규: `신규`, `신입`, `교육`, `new`, `is_new`
- 불가일: `불가일`, `불가`, `휴가`, `unavailable`, `unavailable_dates`

## 자동 배정 기준

이 앱은 단순 랜덤 배정기가 아니라, 아래 점수 기준으로 가능한 사람 중 가장 공평한 후보를 고르는 방식입니다.

1. 월 단위 공평성 우선
2. 평일/휴일 근무 유형별 횟수 별도 균형
3. 개인 불가일 배정 금지
4. 연속 근무와 연속 GY 최소화
5. 신규 인원은 필수 경험 근무 우선 배정

## 현재 버전 메모

- 이 저장소는 실행 가능한 MVP입니다.
- 향후 개선 후보:
  - 여러 달 누적 장기 균형 DB 저장
  - 드래그 범위 일괄 입력 전용 UI
  - 기존 병원/부서 엑셀 양식별 import 매핑 저장
  - OR-Tools 기반 최적화 엔진 추가
