# 경찰청 유실물 물품분류 출처

## 수집 현황

2026-11-24 한국시간에 경찰청 공통코드조회 API `getThngClCd`에서 **대분류 21개, 소분류 85개**를 수집했다. 상위 목록에서 반환된 21개 코드를 각각 조회해 하위 목록을 받았다. JSON의 `status`는 `api_snapshot`이며 JSON과 CSV는 같은 85개 행을 담는다. 각 행에는 대분류 코드·이름과 소분류 코드·이름이 있다. [공공데이터포털 공통코드조회 서비스](https://www.data.go.kr/data/15000651/openapi.do)

습득물·분실물 활용가이드에서 먼저 확인한 아래 예시 두 건의 코드와 이름은 API 응답과 일치했다.

| 대분류 코드 | 대분류명 | 소분류 코드 | 소분류명 | 근거 |
| --- | --- | --- | --- | --- |
| `PRH000` | 지갑 | `PRH200` | 남성용 지갑 | 습득물 활용가이드 v4.0의 분류별 조회 요청 필터와 응답 분류명 |
| `PRA000` | 가방 | `PRA300` | 기타가방 | 분실물 활용가이드 v2.0의 분류별 조회 요청 필터와 응답 분류명 |

## 공식 API 및 활용가이드

- 습득물정보 조회 서비스의 `getLosfundInfoAccToClAreaPd`와 첨부 활용가이드 v4.0을 확인했다. `PRDT_CL_CD_01`과 `PRDT_CL_CD_02`는 각각 대분류와 2단계 분류 입력 필드다. 응답은 `prdtClNm`으로 분류명을 제공한다. [공공데이터포털 습득물정보 조회 서비스](https://www.data.go.kr/data/15058696/openapi.do)
- 분실물정보 조회 서비스의 `getLostGoodsInfoAccToClAreaPd`와 첨부 활용가이드 v2.0을 확인했다. 두 입력 필드의 이름은 습득물 API와 같고, 명세에서는 각각 상위물품코드와 하위물품코드로 표기한다. 두 조회 API의 활용가이드에는 전체 물품분류표가 수록되어 있지 않았다. [공공데이터포털 분실물정보 조회 서비스](https://www.data.go.kr/data/15000799/openapi.do)
- 공통코드조회 서비스의 활용가이드 v2.01은 `getThngClCd`에서 `PRDT_CL_CD_01`을 생략해 상위 코드를 받고, 반환된 각 상위 코드로 하위 목록을 조회하도록 명시한다. 응답의 `prdtCd`와 `prdtNm`은 코드와 이름을, 하위 목록의 `hiPrdtCd`는 상위 연결을 확인하는 데 사용했다. [공공데이터포털 공통코드조회 서비스](https://www.data.go.kr/data/15000651/openapi.do)

## 경찰민원24·LOST112 확인

- 경찰민원24 메인 HTML에서 습득물 검색 메뉴의 연결을 찾았다. 공개 JavaScript의 `getPrdtCdList`는 물품분류 전용 읽기 조회 `POST /lost112/selectPrdtCdList.do`에 `hiPrdtCd`를 보내고, 응답의 `PRDT_CD`를 옵션값, `PRDT_NM`을 표시명으로 사용한다. 빈 값의 ‘선택’ 항목은 화면에서 추가한다. 최상위 분류를 확인하기 위한 요청 한 건은 HTTP 401을 반환했다. 검색 화면의 쿼리 문자열 경로는 robots.txt의 크롤링 제한을 적용해 Playwright 자동 수집을 진행하지 않았다. [경찰민원24 메인](https://minwon24.police.go.kr/main.do) · [분류 선택 JavaScript](https://minwon24.police.go.kr/js/lost112/lost112Common.js) · [robots.txt](https://minwon24.police.go.kr/robots.txt)
- 기존 LOST112의 모바일 및 데스크톱 호스트는 이 환경에서 연결 시간 초과가 발생했다. 경찰민원24 및 기존 LOST112 선택지의 실제 값과 공식 API 코드의 전수 일치 여부는 미확인이다. [기존 LOST112 모바일 검색 화면](https://m.lost112.go.kr/searchIndex.do)
- 분류 전용 API 응답에서 분류 코드, 이름, 상위 연결만 가져왔다. 습득물·분실물 목록/상세 API와 신고 등록·수정 기능은 호출하지 않았다.

## 검증과 재수집

API가 반환한 모든 대분류에 소분류 목록이 있는지 확인했다. 상위 코드와 하위 응답의 `hiPrdtCd`가 일치하며, 코드·이름 공란과 코드 중복은 0건이었다. JSON과 CSV의 행 수와 값이 모두 일치했다.

공통코드조회 활용이 승인된 공공데이터포털의 **Decoding** 인증키를 로컬 환경변수 `DATA_GO_KR_SERVICE_KEY`로 설정한 뒤 아래 명령을 실행한다.

```powershell
python scripts/extract_categories.py
```

스크립트는 상위 목록을 한 번 조회한 후 반환된 상위 코드별로 하위 목록을 순차 조회한다. 응답 오류, 빈 목록, 중복 코드, 상위 연결 불일치가 있으면 기존 파일을 교체하기 전에 종료한다. 완료하면 JSON과 CSV를 갱신하며 JSON에 수집일과 확인된 개수를 기록한다. 웹 옵션값 대조는 별도 확인이 필요하다.
