# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | **한국어** | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> 하나의 아이디어를 중심으로 만든 에이전트 CLI: **에이전트가 무언가를 건드리기 전에 영향 반경(blast radius)부터 파악하세요.**
> 모든 셸 명령은 실행 *전에* 위험 분석과 등급 평가를 거칩니다 — 치명적인 작업은 기본적으로 차단되며, 살아있는 마스코트가 에이전트의 현재 상태를 그대로 보여줍니다.

순수 Python, 컴파일 불필요, 모델 중립. 119개 모듈, 약 21k 줄의 소스 코드를 **591개 오프라인 테스트**(목(mock) 서버 엔드투엔드 포함)가 뒷받침하며, Python 3.11–3.13 CI 매트릭스에서 검증됩니다.

<!-- 📹 TODO(demo): 여기에 30–60초 길이의 터미널 녹화 화면을 추가해 주세요:
     qxt chat → 작업 수행 → 마스코트 상태 전환 → 안전 차단 순간을 보여주는 내용.
     그때까지는 아래의 ASCII 상태 머신이 실제 모습입니다. -->

## 마스코트가 곧 상태 표시줄입니다

Qingxiaotuan("작은 초록 만두")은 고정된 로고가 아니라 **생명 징후를 지닌 상태 머신**입니다. 터미널에 실시간으로 렌더링되므로 에이전트가 지금 무엇을 하고 있는지 항상 알 수 있습니다:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| 상태 | 시점 | 시각 표현 |
| --- | --- | --- |
| `idle` | 입력 대기 중 | 차분한 호흡 |
| `thinking` | 모델 추론 중 | 좌우로 흔들림 + 스피너 |
| `working` | 도구 호출 실행 중 | 부유 + 진행 링 |
| `alert` | 위험한 명령을 **안전 가드레일이 차단**함 | 주황색으로 변하고 몸을 떱 — *눈으로 보이는* 영향 반경 제어 |
| `done` | 작업 완료 | 초승달 눈 + 반짝임 |

## 왜 이 프로젝트인가

- **기본값은 최소 영향 반경(Minimum Blast Radius)** — 모든 셸 명령이 실행되기 전에 순수 Python `safety` 엔진이 정적 위험 분석을 수행합니다. `critical` 등급 명령(`rm -rf /`, `git push --force`, `DROP TABLE`)은 실행 후 기록만 남기는 수준이 아니라 **실행 전에 차단**됩니다.
- **되돌릴 수 있는 워크스페이스 변경** — `/diff`로 변경 사항을 검토하고, `/undo`로 롤백합니다(`--safe` 모드 포함). 세션은 append-only 이벤트 스트림으로 재생할 수 있습니다.
- **10개 기능 엔진, 모두 순수 Python, IPC 제로** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market 엔진이 헬스 체크를 갖춘 레지스트리로 연결됩니다(`qxt ext selftest`).
- **모델 중립적** — OpenAI 호환 / Anthropic 어댑터 뒤에서 DeepSeek, Claude, Gemini 또는 로컬 모델을 사용할 수 있으며, `/model`로 언제든 전환합니다. `/route`는 작업 난이도를 추정해 모델을 제안합니다(권고용).
- **멀티 에이전트 스웜** — `/swarm`은 강력한 모델로 계획을 세우고, 저렴한 모델들이 하위 작업을 병렬로 실행하게 한 뒤, 강력한 모델이 결과를 수용하거나 거부합니다.
- **자기 개선 루프** — 각 실행이 끝나면 리플렉터(reflector)가 실패 원인을 진단하고(pytest/npm/git/cargo/dotnet 인식) 가드레일로 정리하여, 같은 실수는 다음 번에는 더 일찍 잡아냅니다.
- **재시작 후에도 유지되는 메모리** — 세션 / 팩트 / 스킬의 세 계층으로 구성되며, SQLite FTS5 전문 검색으로 세션 경계를 넘어 기억을 다시 불러옵니다.

## 빠른 시작

> Python ≥ 3.11 필요. 순수 Python — 컴파일할 것이 전혀 없습니다.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

헤드리스 one-shot 작업도 지원합니다:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **암호화 참고:** `crypto` 엔진은 표준 라이브러리만 사용합니다(PBKDF2-HMAC-SHA256 키 유도 + SHA256-keystream 스트림 암호 + SHA-256 지문). 이 스트림 암호는 인증 태그가 없는 경량 설계로, 변조 감지가 필요한 로컬 용도에는 적합하지만 고보안 프리미티브는 아닙니다.

## 슬래시 명령

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

주요 명령:

| 명령 | 기능 |
| --- | --- |
| `/plan` | 읽기 전용 분석 모드 — 변경(mutation) 도구는 가로채집니다 |
| `/swarm` | 멀티 에이전트 협업 (강력한 모델의 계획 → 저렴한 모델의 병렬 실행 → 강력한 모델의 검수) |
| `/route` | 난이도 추정 + 모델 제안 (권고용, 자동 전환 없음) |
| `/compact` | 오래된 히스토리를 접어 컨텍스트 예산 확보 |
| `/cost` | 토큰 사용량, 캐시 적중률, 예상 비용 |
| `/undo` | 워크스페이스 빠른 롤백 (`all` / `--safe` / 파일별) |

## 기능 엔진

| 엔진 | 제공 기능 |
| --- | --- |
| `diff` | 줄/단어 수준 diff + patch + 3-way 병합 |
| `crypto` | PBKDF2-HMAC-SHA256 키 유도 + SHA256-keystream 스트림 암호 + 지문(fingerprint) |
| `index` | FNV-1a 증분 심볼 인덱스 |
| `ansi` | 터미널 이스케이프 파싱 / 제거 / 렌더링 |
| `safety` | 최소 영향 반경 가드레일: 위험도 점수 산정 + 영향 반경 산출 + 차단 |
| `json` | RFC 6901 포인터 / 경로별 diff / deep merge |
| `search` | 재귀 정규식 검색 (node_modules/.git 제외) |
| `notify` | 크로스 플랫폼 데스크톱 알림 |
| `rules` | YAML 정책 검증 (eval 없는 안전 표현식) |
| `skill-market` | 스킬 패키지 레지스트리: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## 아키텍처

```
qingxiaotuan/
├── cli/          command layer (+ fullscreen TUI)
├── core/         kernel & orchestration: kernel / agent / devloop / background / swarm
├── config/       defaults / loader / plugin / validate
├── ext/          10 pure-Python engines + registry
├── models/       adapters: openai_compat / anthropic / provider_catalog / router
├── memory/       store (SQLite FTS5) / sessions (append-only events)
├── skills/       manager / plugin
├── tools/        base / shell / filesystem / web / external / code / audit
├── context/      indexer / manager
├── cron/         scheduled jobs
├── ui/           repl + fullscreen TUI (shared theme)
└── resources/    bundled skill templates + SOUL.md identity
```

## 품질

- **591개 오프라인 테스트** — 네트워크 없이 실행할 수 있으며, 로컬 OpenAI 호환 목 서버를 대상으로 한 엔드투엔드 실행(에이전트 도구 루프, 스트리밍, 백그라운드 워커, 실제 `qxt` 서브프로세스)을 포함합니다.
- **CI 매트릭스**: Python 3.11 / 3.12 / 3.13.
- 런타임 의존성은 단 다섯 개: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## 로드맵

- [ ] 자동 모델 라우팅 (`router.enabled`를 에이전트 루프에 연동; 현재 `/route`는 권고용)
- [ ] PyPI 배포 (`pip install qingxiaotuan`)
- [ ] 스킬 마켓 공개 레지스트리

## 기여하기

Issue와 PR은 언제나 환영합니다 — [CONTRIBUTING.md](./CONTRIBUTING.md)를 참고하세요. 이 README의 번역은 파일 안에서 함께 관리하며, 어떤 언어든 PR로 수정해 주세요.

## 라이선스 및 크레딧

MIT — [LICENSE](./LICENSE)를 참고하세요.

오픈 소스의 어깨 위에서 — 다음 프로젝트들의 아이디어를 받아들여 Python으로 처음부터 새로 구현했습니다:

- **DeepSeek Harness (dsh)** — 마이크로커널(Cordis 스타일), everything-is-a-plugin 철학, 프로필 기반 설정, 모델 중립 어댑터, 헤드리스 작업, append-only 세션 이벤트 스트림
- **Hermes Agent** — 3계층 메모리, 스킬 자기 진화 루프, SOUL.md 정체성, SQLite FTS5 세션 간 회상, cron 작업
- **Claude Code** — 매끄러운 CLI 인터랙션, 실시간 thinking/도구 상태 표시, 대형 컨텍스트 관리, 대화형 슬래시 명령
