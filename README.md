# 습득물 공고 센터

습득기관 담당자가 사진과 습득 정보를 제출하고, 분석 결과와 공개 범위를 검토한 뒤 공고를 로컬 데이터베이스에 등록하는 PoC입니다. React와 TypeScript로 만든 웹 화면은 FastAPI와 연동합니다. 통합 공고 목록과 상세는 담당자 접속 없이 조회할 수 있습니다.

사진과 메모를 사람이 일일이 옮길 때 물품별 기록 품질이 달라지고, 사진 속 문자나 자유 입력에 포함된 개인정보가 공고로 넘어갈 수 있습니다. 이 PoC는 경찰청 공식 분류와 물품별 입력 서식을 적용하고, AI가 제안한 정보에 코드 검증과 담당자 검토를 더합니다.

```mermaid
flowchart LR
    React[React 담당자 화면] --> API[FastAPI]
    API --> Registration[등록 에이전트 · 로컬 Ollama]
    Registration --> Schema[공식 분류 · 동적 스키마 · 근거]
    Schema --> Verification[검증 에이전트]
    Schema --> Privacy[Privacy Agent · PaddleOCR · OpenCV]
    Verification --> Supervisor[검토 상태 통합]
    Privacy --> Supervisor
    Supervisor --> Staff[담당자 수정 · 마스킹 · 승인]
    Staff --> Gate[게시 강제 검사]
    Gate --> DB[(PostgreSQL 공개 공고)]
    DB --> Public[공개 목록 및 상세]
```

프론트엔드는 React, TypeScript, Vite, Tailwind CSS를 사용합니다. 백엔드는 Python, FastAPI, LangGraph, SQLAlchemy를 사용합니다. Mock Mode의 기본 DB는 SQLite이며 PostgreSQL로 변경할 수 있습니다.

| 실행 모드 | 분석 | 저장소 |
| --- | --- | --- |
| **Mock Mode** | 업로드한 사진의 색상과 형태에 반응하는 가상 분석. 화면과 공고 API에 모드 표시. OCR 호출 없이 사진 전체를 가림 | 기본 SQLite, PostgreSQL로 변경 가능 |
| **Real Mode** | 로컬 Ollama VLM으로 추출과 사진 대조 검증, LLM으로 공개 후보 심사, PaddleOCR로 이미지 문자 영역 검출 | 별도 PostgreSQL DB 권장 |

## 1. Windows 및 VS Code 준비

1. VS Code에서 저장소 폴더를 열고 **터미널 → 새 터미널**에서 PowerShell을 선택합니다.
2. Real Mode의 PaddlePaddle Windows 휠과 호환되는 **64비트 Python 3.12**를 권장합니다. VS Code의 **Python: Select Interpreter**에서 프로젝트의 `.venv\Scripts\python.exe`를 선택합니다.
3. 저장소 루트에서 아래 명령을 실행합니다. 이후 명령은 가상환경을 활성화하지 않아도 동작합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Python을 Conda로 설치해 `py -3.12`를 사용할 수 없다면 Python 3.12 환경에서 `python -m venv .venv`를 실행합니다. 가상환경을 활성화하려면 `. .\.venv\Scripts\Activate.ps1`을 실행합니다.

기존 가상환경의 Python 버전을 바꾸려면 새 가상환경을 만들고 의존성을 설치합니다. 이전 버전의 바이너리 패키지를 포함한 폴더에 `venv`를 다시 실행하면 패키지 호환 문제가 생길 수 있습니다. 다른 가상환경으로 실행할 때는 `start.ps1 -PythonPath`에 해당 Python 경로를 지정합니다.

4. [Node.js 공식 다운로드](https://nodejs.org/en/download)에서 Windows용 Node.js 22 LTS(22.12 이상)를 설치하고 VS Code 터미널을 새로 엽니다. 잠금 파일을 기준으로 프론트엔드 패키지를 설치합니다.

```powershell
node --version
npm.cmd --version
Push-Location .\frontend
npm.cmd ci
Copy-Item .env.example .env.local
Pop-Location
```

루트 `.env.example` 설정으로 Mock Mode를 사용할 수 있습니다. 화면의 **담당자 접속**에서 접근 키 `local-mock-only`를 입력합니다. PowerShell 환경변수로 지정한 값은 `.env` 값보다 우선합니다. `frontend/.env.local`의 `BACKEND_URL`은 Vite 서버의 프록시 목적지입니다. 접근 키는 루트 `.env`의 `STAFF_TOKEN`에 설정합니다.

## 2. PostgreSQL 설정

[PostgreSQL Windows 설치 안내](https://www.postgresql.org/download/windows/)에서 서버와 명령줄 도구를 설치합니다. 설치 중 지정한 관리자 암호와 포트(기본값 `5432`)로 SQL Shell(psql)에 접속합니다. 아래 예시는 18 버전입니다. 설치 버전에 따라 경로를 조정합니다.

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

psql에서 전용 역할과 DB를 생성합니다. `\password`는 암호를 대화형으로 받습니다.

```sql
CREATE ROLE laf_app LOGIN;
\password laf_app
CREATE DATABASE laf_mock OWNER laf_app ENCODING 'UTF8';
CREATE DATABASE laf_real OWNER laf_app ENCODING 'UTF8';
\q
```

`.env`의 `DATABASE_URL`에 사용할 DB를 지정합니다. 암호에 `@`, `:`, `#` 등이 있으면 URL 인코딩합니다. Mock에서는 `laf_mock`, Real에서는 `laf_real`을 선택합니다.

```dotenv
DATABASE_URL=postgresql+psycopg://laf_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/laf_mock
```

```powershell
.\.venv\Scripts\python.exe -m backend.init_db
```

앱 시작 시에도 초기화가 실행됩니다. DB 연결 오류가 나면 PostgreSQL Windows 서비스의 실행 상태와 포트, 역할 암호, DB 이름을 확인합니다. SQLite는 파일 `instance/lost_and_found_mock.db`를 사용합니다.

## 3. Real Mode의 VLM, LLM 및 OCR

1. [Ollama Windows 설치 안내](https://docs.ollama.com/windows)대로 Ollama를 설치합니다. 아래는 별도 터미널에서 서버를 실행하는 예입니다. Windows 트레이에서 Ollama가 이미 실행 중이면 먼저 종료하고 해당 설정으로 시작합니다.

   ```powershell
   $env:OLLAMA_HOST = "127.0.0.1:11434"
   $env:OLLAMA_NO_CLOUD = "1"
   ollama serve
   ```

2. 다른 터미널에서 모델을 확인합니다. 단독 테스트와 동일한 `qwen3-vl:8b`를 사진 추출에 사용합니다. 독립 사진 검증에는 `VERIFICATION_VLM_MODEL`을 지정할 수 있습니다. 생략하면 추출 모델을 재사용합니다. 현재 검증 구성은 `qwen3.5:9b`를 사진 검증과 공개 후보 심사에 사용합니다.

   ```powershell
   ollama pull qwen3-vl:8b
   ollama pull qwen3.5:9b
   ollama list
   Invoke-RestMethod http://127.0.0.1:11434/api/tags
   ```

3. OCR 의존성을 설치합니다. 첫 분석 시 PaddleOCR가 PP-OCRv5 모바일 탐지 모델과 한국어 인식 모델을 내려받아 캐시에 저장합니다. 모델 다운로드와 로딩이 포함된 첫 분석에는 시간이 더 걸릴 수 있습니다. [PaddleOCR 설치와 실행](https://www.paddleocr.ai/main/en/version3.x/installation.html) · [OCR 파이프라인](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html).

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-real.txt
   .\.venv\Scripts\python.exe -c "import paddle; paddle.utils.run_check()"
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. `.env`를 변경합니다. 새로 생성한 접근 키를 저장하고 브라우저에서 접속할 때 사용합니다.

   ```dotenv
   APP_MODE=real
   DATABASE_URL=postgresql+psycopg://laf_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/laf_real
   STAFF_TOKEN=GENERATED_RANDOM_TOKEN
   OLLAMA_URL=http://127.0.0.1:11434
   VLM_MODEL=qwen3-vl:8b
   VERIFICATION_VLM_MODEL=qwen3.5:9b
   LLM_MODEL=qwen3.5:9b
   MODEL_TIMEOUT=240
   MAX_REEXTRACTIONS=2
   MAX_QUESTIONS=2
   REQUIRE_OFFICIAL_CATEGORY=false
   ```

Ollama 주소에는 루프백 주소를 사용하고 로컬에서 다운로드한 모델을 지정합니다. VLM과 LLM을 바꿀 때는 해당 모델이 [Ollama 구조화 JSON 출력](https://docs.ollama.com/capabilities/structured-outputs)을 지원하는지 확인합니다. 시작 전에 `APP_MODE`를 `real` 또는 `mock`으로 명시합니다.

모델 호출 실패 시 화면에서 연결 실패, 모델 미설치, 이미지 입력 오류, 구조화 오류 또는 타임아웃을 확인한 뒤 **사진 재분석**을 선택할 수 있습니다. 상세 특징 추출에 실패하면 먼저 인식한 물품명과 유형을 유지합니다. OCR을 완료하지 못한 경우 VLM 시각 분석은 유지하고 사진 공개를 제한합니다. `REQUIRE_OFFICIAL_CATEGORY=true`에서는 공식 대분류·소분류가 모두 매핑된 초안만 승인 및 게시할 수 있습니다. 기본 설정에서는 공식 매핑을 검토하는 동안에도 관찰 유형과 검증된 공개 초안을 확인할 수 있습니다.

## 4. 실행, 접속 주소 및 API 연동

Windows에서 검증한 조합은 Python 3.12 x64, Node.js 22, PaddlePaddle 3.3.1, PaddleOCR 3.7.0, OpenCV contrib 4.10.0.84입니다. Real Mode 패키지는 새 가상환경에 설치하고, 동일 가상환경에 별도의 OpenCV 배포판을 중복 설치하지 마세요. 설치 후 해당 Python에서 `python -m pip check`로 의존성을 확인합니다. 얼굴 검출기 파일은 메모리에서 로드하므로 프로젝트 경로에 한글이 있어도 동작합니다.

VS Code PowerShell에서 동시에 실행합니다.

```powershell
.\start.ps1
```

실행 정책으로 스크립트가 차단되면 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1`을 사용합니다. 실행 중인 터미널에서 `Ctrl+C`를 누르면 두 서비스가 종료됩니다. 로그는 `instance/logs/`에 기록됩니다. 기본 포트가 이미 사용 중이면 `-BackendPort 18000 -FrontendPort 5173`처럼 지정합니다. 브라우저 자동 열기를 생략할 때는 `-NoBrowser`를 전달합니다.

| 접속 대상 | 기본 주소 |
| --- | --- |
| React 웹 서비스 | [http://127.0.0.1:5173](http://127.0.0.1:5173) |
| FastAPI 문서 | [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) |
| 백엔드 및 DB 상태 | [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) |

VS Code의 두 터미널에서 각각 실행할 수도 있습니다.

```powershell
# 터미널 1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

```powershell
# 터미널 2
Set-Location .\frontend
$env:BACKEND_URL = "http://127.0.0.1:8000"
npm.cmd run dev
```

React는 같은 주소의 `/api/*`에 요청합니다. Vite 개발 서버와 미리보기 서버는 `/api` 경로 접두사를 제거해 FastAPI로 전달합니다. API 포트를 바꿀 때는 프록시 목적지도 변경합니다. 스크립트로 시작하면 지정한 백엔드 포트를 프록시에 전달합니다.

프론트엔드 디렉터리에서 배포용 번들을 생성하고 확인할 수 있습니다.

```powershell
npm.cmd run build
npm.cmd run preview
```

실제 배포에서는 웹 서버가 `frontend/dist`를 제공하고 페이지 URL을 `index.html`로 연결하도록 구성합니다. 같은 HTTPS 출처의 `/api/*`를 FastAPI로 프록시하고 원래 호스트와 HTTPS 정보를 전달해야 담당자 세션과 출처 검사가 작동합니다.

## 5. 브라우저 수동 테스트

```powershell
.\.venv\Scripts\python.exe scripts/make_sample_photo.py
```

| 단계 | 조작 | 확인할 결과 |
| --- | --- | --- |
| 1. 사진 첨부 | **습득물 등록**에서 `instance/sample-umbrella.png`를 끌어다 놓거나 **사진 선택**으로 첨부합니다. 날짜, 시간, 지역, 장소, 습득자명, 보관기관을 입력합니다. | 첨부 수와 미리보기가 표시됩니다. 사진마다 삭제할 수 있고, 필수 입력은 오류 메시지와 키보드 포커스로 안내합니다. |
| 2. 분석 | **AI 분석 시작**을 누릅니다. 접속 창이 뜨면 접근 키를 입력합니다. | 진행 상태와 표준화한 물품 정보, 검증 결과, 근거가 표시됩니다. Mock Mode의 테스트 사진은 검정 우산으로 처리됩니다. |
| 3. 추가 확인 및 수정 | **추가 정보 확인**에서 제목을 `시청역 검정 우산`으로 바꿉니다. **습득 기본 정보 수정**을 열어 가상 메모 `연락 010-0000-0000`을 넣고 **수정 및 재검증**을 누릅니다. | 비공개 메모에 `연락 010-****-****`이 표시됩니다. |
| 4. 마스킹 | **마스킹 사진 보기**를 열고 사진을 드래그하거나 좌표를 입력해 **추가 마스킹 및 재검사**를 누릅니다. | 담당자 지정 영역과 처리 상태가 표시됩니다. Mock Mode는 사진 전체를 가리며 사진 공개가 제한됩니다. |
| 5. 충돌 및 재분석 | 별도 건에서 메모에 `색상: 파랑`을 입력한 뒤 같은 우산 사진을 분석합니다. | 색상 충돌과 확인 요청이 나타납니다. 메모를 바로잡아 재검증하거나 최대 두 번 사진을 재분석할 수 있습니다. |
| 6. 승인 및 등록 | 검토 확인 상자를 선택하고 **담당자 승인**, **공고 등록**을 누릅니다. | 승인 완료와 공고 등록 완료가 순서대로 표시됩니다. |
| 7. 목록 및 검색 | **통합 공고 목록**에서 제목이나 장소를 검색하고 분류 필터에 우산을 선택합니다. 공고를 선택해 상세를 엽니다. | 공개 필드가 표시됩니다. 검색 결과가 8건을 넘으면 페이지를 이동할 수 있습니다. 담당자 접속을 해제한 뒤에도 조회할 수 있습니다. |
| 8. 로그 | 담당자로 접속한 상태에서 **실행 로그**와 관리번호를 선택합니다. | 버전, 에이전트 순서, 실행 시간, 재추출 횟수와 분기가 표시됩니다. |

Real Mode에서 [가상 카드 사진](tests/fixtures/privacy-card.png)을 올려 탐지 결과와 게시 제한을 확인합니다. 판독, 시각 판정 또는 재검사가 불확실하면 사진 공개를 차단합니다. 검사를 통과한 후보 이미지를 게시하려면 담당자가 사진을 확인하고 사진 포함 옵션을 선택합니다. [개인정보 정책과 테스트 절차](docs/privacy-masking.md)

## 6. 자동 테스트

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

기본 테스트는 사진 업로드, 모델 오류, 정확성 충돌, 비공개 정보와 이미지 마스킹, 재추출 한도, 승인 무효화, 게시 전 정책 검사, 등록·검색 및 민감정보 분리를 검증합니다. PostgreSQL은 테스트 전용 DB를 지정합니다.

```powershell
# SQL Shell에서 전용 테스트 DB 생성
# CREATE DATABASE laf_test OWNER laf_app ENCODING 'UTF8';
$env:LAF_TEST_POSTGRES_URL = "postgresql+psycopg://laf_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/laf_test"
.\.venv\Scripts\python.exe -m pytest -q tests/test_api.py
```

Playwright는 별도 포트에서 React와 FastAPI를 시작하고 독립된 Mock DB에 공고를 등록합니다. 시스템 Chrome이 있다면 설치 단계를 생략하고 `$env:LAF_BROWSER_CHANNEL = "chrome"`을 지정합니다. 다른 가상환경을 쓸 때는 `LAF_PYTHON`으로 Python 실행 파일의 절대 경로를 지정합니다. PostgreSQL로 E2E를 실행할 때는 테스트 전용 DB 주소를 `LAF_E2E_POSTGRES_URL`에 지정합니다.

```powershell
Set-Location .\frontend
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

실제 모델로 자동 검증하려면 Ollama를 시작하고 같은 터미널에 모델 이름을 설정한 뒤 다음 명령을 실행합니다. 기본값은 테스트별 임시 SQLite이며, PostgreSQL을 사용하려면 `LAF_REAL_TEST_POSTGRES_URL`에 전용 테스트 DB를 설정합니다.

```powershell
$env:LAF_TEST_REAL = "1"
$env:OLLAMA_URL = "http://127.0.0.1:11434"
$env:VLM_MODEL = "qwen3-vl:8b"
$env:VERIFICATION_VLM_MODEL = "qwen3.5:9b"
$env:LLM_MODEL = "qwen3.5:9b"
.\.venv\Scripts\python.exe -m pytest -q tests/test_real.py
```

원본 모델 응답은 개인정보가 없음을 확인한 테스트 사진에 한해 별도 진단 명령에서 확인할 수 있습니다. 명령은 테스트 사진의 로컬 분석을 다시 실행하고 표준 출력으로 원본 응답, 파싱된 값, 검증 결과, 공고 미리보기를 보여줍니다. 웹 실행 로그에는 모델명, 이미지 입력 여부, 처리 시간, 오류 코드와 건수만 표시됩니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.inspect_vlm "C:\path\non-sensitive-fixture.png" --non-sensitive-fixture
```

## 7. 구조와 공개 경계

| 경로 | 역할 |
| --- | --- |
| `backend/` | FastAPI, LangGraph, DB 모델, 공식 분류, 개인정보 탐지와 게시 검사 |
| `backend/policies/` | 물품별 서식, 개인정보 유형과 마스킹·공개 정책 |
| `data/` | 경찰청 API 분류 스냅샷과 검토용 CSV |
| `frontend/` | React 화면과 Playwright 시나리오 |
| `scripts/`, `tests/` | 분류 데이터 갱신, 합성 이미지 생성, API 및 모델 검증 |

Registration Agent가 물품과 실제 관찰 유형을 추출합니다. Evidence Tracker가 공식 분류와 필드 근거를 연결합니다. Verification Agent는 사진과 추출값을 대조하고 Privacy Agent는 개인정보와 부정수령에 쓰일 정보를 심사합니다. Supervisor는 승인 가능 여부를 결정하고 Adaptive Question Agent는 담당자가 보완할 항목을 표시합니다.

React Router가 등록, 분석, 추가 확인, 승인, 공고 목록, 공고 상세, 실행 로그의 일곱 페이지를 제공합니다. FastAPI는 사진과 폼을 받아 초안을 저장하고 백그라운드로 기존 분석을 실행합니다. 분석 진행 중에는 브라우저가 에이전트별 처리 상태를 조회합니다. 분석이 중단된 초안은 분석 화면에서 재분석할 수 있습니다.

LangGraph는 Verification Agent와 Privacy Agent를 병렬 실행합니다. 수정과 재검증은 이전 승인을 무효화하며 최종 등록 시 서버가 승인 상태와 공개 정책을 다시 검사합니다. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)

- `backend/policies/item_schemas.json`에서 물품별 특징과 공개 범위를 정의합니다. `data/categories.json`은 경찰청 API에서 확보한 대분류 21개와 소분류 85개의 기준 데이터입니다. CSV로 데이터 일치 여부를 검증하며 경찰민원24 웹 옵션과의 일치는 확인되지 않았습니다. [동적 물품 스키마와 테스트](docs/dynamic-item-schemas.md)
- 분류 갱신에는 공공데이터포털 승인 키를 로컬 환경변수 `DATA_GO_KR_SERVICE_KEY`로 설정하고 `python scripts/extract_categories.py`를 실행합니다. [분류 출처와 검증 절차](docs/category_source.md)
- 공개 API는 별도의 공고 및 공개 사진 테이블을 조회합니다. 원본은 검증에 쓰고 게시하거나 담당자가 폐기할 때 초안에서 제거합니다. OCR 원문은 작업 메모리에서 처리하며 탐지 유형, 좌표와 마스킹 표시만 기록합니다. 공개 사진은 검증 후 기록한 해시와 이미지 내용이 일치할 때만 제공합니다.
- 접근 키를 입력하면 FastAPI가 만료 시간이 있는 서명 세션 쿠키를 발행합니다. 쿠키는 `HttpOnly`와 `SameSite=Strict`를 사용합니다. 설정된 접근 키는 프론트엔드 번들에 포함되지 않습니다. 기존 Bearer 헤더는 직접 API 테스트에 사용할 수 있습니다. 일반 공고 목록과 상세는 별도 공개 API에서 공고 데이터를 가져옵니다.
- 기본 실행 범위는 로컬 사용자 테스트입니다. 공개 심사는 규칙, 모델 및 담당자 검토를 결합하며 기관 운영 전에는 기관별 사용자 권한, 데이터 보관 정책, 실제 데이터로 개인정보 탐지율 평가가 필요합니다.
- 분실물 매칭, 소유자 확인, 경찰청 시스템 연동은 이후 개발 범위입니다.

## 8. 디자인 출처

행정안전부의 범정부 UI/UX 디자인 시스템(KRDS)의 공개 색상 토큰과 입력 폼, 첨부파일, 진행 단계, 페이지네이션 패턴을 사용했습니다. 로컬 React 컴포넌트와 Tailwind CSS로 구현했으며, 경찰민원24 참고 화면의 넓은 입력 간격과 섹션 구분을 자체 서비스명과 레이아웃에 적용했습니다. [KRDS 개발자 가이드](https://www.krds.go.kr/html/site/outline/outline_03.html) · [컴포넌트](https://www.krds.go.kr/html/site/component/component_summary.html) · [공공누리 제1유형 이용 조건](https://www.krds.go.kr/html/site/utility/utility_06.html).

Chrome에서 너비 1440px와 390px로 등록 화면을 캡처해 참고 화면과 비교했습니다. 데스크톱에서는 섹션 안쪽 여백 24px, 입력창 최소 높이 52px, 주요 버튼 높이 48px를 적용했습니다. 너비 390px에서는 날짜와 시간만 나란히 두고 주소와 기관 정보는 한 열로 배치해 예시 문구가 보이고 가로 스크롤이 생기지 않는지 확인했습니다.
