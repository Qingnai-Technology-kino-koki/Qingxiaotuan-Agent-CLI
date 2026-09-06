# 青小團 / Qingxiaotuan Agent CLI

> **「Model + Harness = Agent」** — "생각"과 "안전하게 굴리기"를 갈라서, 둘 다 열쇠를 손에 쥐여줍니다.
> 안전 우선 · 모델 무관 · 순수 Python AI Agent Harness. `v0.2.014` · MIT · Python ≥ 3.10

**언어/Language:** [English](README.md) · [简体中文](README_zh-CN.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · **한국어** · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Русский](README_ru.md)

**깔아보기 전에:** [SECURITY.md](SECURITY.md)（위협 모델）· [CHANGELOG.md](CHANGELOG.md)（버전 규율）· [ARCHITECTURE.md](ARCHITECTURE.md)（아키텍처）· `qxt models list-providers`

---

## 한 줄 요약

남들은 "모델 하나에 껍데기 씌우기". 칭샤오퇀은 반대——**뇌 갈아끼우기 가능한 몸통**을 줍니다. 48개 프로바이더 핫스왑, 완전 오프라인, 실수는 되돌리고, 명령 실행**전에** 영향 범위를 보여줍니다. **생각은 모델이, 그걸 받쳐주는 건 이 녀석이.**

**이건 아니다**: 특정 모델 전용 함체 아님(핫스왑·자체호스팅·오프라인 자유); IDE의 수행원 아님(표준 터미널 도구, ACP로 VSCode/Zed/JetBrains가 몰아붙임); 한 겹짜리 취약한 추상체도 아님(개발자용 마이크로커널 + 일반인용 원샷 `setup`).

---

## 뭐가 다르냐

| 이거 | 어디까지 |
|---|---|
| 🛡️ **안전 우선** | 4중 게이트: 정적 리스크 판정, 영향 범위 사전 차단, YOLO 레드라인 바닥, 트랜잭션 원장으로 정밀 `/undo` |
| 🔌 **모델 무관** | 48 프로바이더 + 로컬 Ollama + 핫스왑 + 자동 라우팅（`router.*`） |
| 🧠 **세 가지 메인 루프** | ReAct / Planner-Execute / DevLoop 플러그형 — 하나의 커널, 여러 '사고 리듬' |
| 🔧 **마이크로커널** | 한 줄 `@plugin`, 서비스 레지스트리, append-only 이벤트 버스, hook 미들웨어 |
| 🗂️ **메모리** | SQLite FTS5 + 세션 이벤트 스트림; 3계층 메모리, `/undo`, checkpoint, replay, Trajectory 내보내기 |
| 🧩 **생태계** | MCP + ACP — 도구를 꽂거나, IDE한테 몰아붙여짐 |
| 🌍 **10개 언어** | 기본 중국어 포함 전면 로컬라이즈 |
| 🐍 **순수 Python** | 약 414 `.py` / 약 7.7만 줄 / 32 패키지 / 15+ 플러그인, MIT |

---

## 솔직히 말해서

**Q: 이거 진짜 '독자 개발'이야? 숨기는 거 없지?**
커널과 대부분 기능(`kernel/`·`core/`·`acp/`·`tools/`·`ports/`)은 Python으로 **아키텍처부터 한 줄 한 줄 자체 구현**이고, 외부 프로토콜과의 상호운용을 위해서만 인터페이스를 맞췄습니다. 유일하게 일부러 남긴 예외 하나: `--tui` 터미널 UI는 **Kimi Code의 시그니처 스타일**（`#4FA8FF` 메인, 달 모양 스피너, 두 줄 상태바）을 의도적으로 따랐습니다——"손맛 좋으면 새로 안 만든다." 이 부분만 다른 프로젝트의 껍데기를 입었고, 크레딧은 [NOTICE](NOTICE). 나머지는 전부 칭샤오퇀의 살과 피입니다.

**Q: 왜 명령 실행 전에 막는데?**
기능이지 버그 아님. 위험 명령은 원래 확인을 받고, YOLO도 하드 레드라인은 못 어김. 귀찮으면 `qxt safe allow <cmd>`로 허용목록에——안전을 끄는 게 아니라.

**Q: `/undo`로 진짜 뭘 되돌리는데?**
원장에 기록된 모든 **쓰기**: 단일 파일, 단일 step, 턴 전체. 내부는 트랜잭션 원장 + diff `reverse_transform` + checkpoint 스냅샷. 만능은 아니지만, "까먹음 = 확실한 손해"를 "아마 건질 수 있음"으로 바꿉니다.

**Q: 내 개인 파일 읽을까 걱정돼.**
권한 정책은 domain allow-list로 도구 범위를 제한, 유출 방지용 egress 제어, 출력의 키는 마스킹.

**Q: 완전 오프라인은?**
`qxt models local`로 탐지, `/offline`으로 Ollama 관리. 인터넷 없어도 굴러갑니다.

**Q: 내 도구/플러그인 만들 수 있어?**
당연. `@plugin` 메타데이터 선언, `activate(kernel)` 안에서 `kernel.provide(...)` / `kernel.require(...)`. 3스텝:

```python
from ..core.kernel import Kernel, Plugin

@plugin("my.tool", provides=["loop_registry"])
class MyTool(Plugin):
    def activate(self, kernel):
        def _handler(ctx, query: str) -> str:
            return f"echo: {query}"
        kernel.provide("tools.my", _handler)
```

---

## 퀵스타트

```bash
git clone <이 레포> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

qxt setup
# 수동으로도: ~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # 키는 절대 평문으로 안 씀

qxt            # 대화
qxt --print run "안녕? 너를 한 줄로 소개해 봐."
```

---

## 커맨드 면: 25 서브커맨드 + 36 슬래시 커맨드

| 커맨드 | 용도 |
|---|---|
| `qxt` | 대화형 TUI (Kimi Code 스킨) |
| `qxt setup` / `qxt models` | 프로바이더 설정 / 48개 모델 목록 |
| `qxt agent` | 이름 붙은 에이전트 (`.claude/agents` 호환, 3계층 발견) |
| `qxt acp` | ACP server 켜서 VSCode / Zed / JetBrains가 컨트롤하게 |
| `qxt cron` | 정기 백그라운드 작업 |
| `qxt doctor` / `qxt bench` | 건강검진 / 벤치 |
| `qxt arch demo` | 5계층 아키텍처 한 방에 검증 |

애용 슬래시 커맨드: `/plan`·`/model`·`/undo`·`/impact`·`/swarm`·`/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help` — 전체는 TUI에서 `Ctrl-G`.

---

## 세 가지 메인 루프, 같은 안정감

- **ReActLoop**: 생각→행동→확인, 기본 리듬.
- **PlannerExecuteLoop**: 강한 모델이 계획, 싼 모델이 실행（`router.*`로 구획 분리 지원）. 토큰 아끼는 장인.
- **DevLoop**: 쓰고→검증하고→자체 치유. `/verify`가 프로젝트 종류(Python/Node/Rust/Go) 자동 판별, 테스트 커맨드 추론 후 최대 N라운드 수리.

## 안전 모델: 4중 게이트 + 원장식 취소

1. **정적 스코어링** — 모든 셸 커맨드를 `safety_engine.score()`로 `none→critical` 판정, 간접 전개 처리(IFS, `$VAR`, 커맨드 치환, ANSI-C/8진/16진 이스케이프, PowerShell Base64, Unicode NFKC; 재귀 ≤32층).
2. **영향 범위 사전 차단** — 실행**전에** 닿을 범위를 보여줌（`--impact`）。
3. **YOLO 레드라인 바닥** — YOLO는 단계별 확인을 없앨 수 있지만 하드 레드라인（재귀 `rm`, force-push, `chmod -R 000 /`…）은 **절대 자동 실행 불가**.
4. **트랜잭션 원장** — 모든 쓰기 기록, `/undo`는 diff `reverse_transform` + 스냅샷으로 정밀 복원.

규칙은 늘 `deny > ask > allow`. `qxt safe allow <cmd>`로 허용목록을 쓰지, 안전을 꺼서 클릭 수를 줄이지 마세요.

---

## 아이디어 굴리는 곳

- **메모리** — 3계층(유저/프로젝트/세션) + SQLite FTS5(trigram), 못 쓰면 일반 텍스트로 자동 강등.
- **서브에이전트** — 타입드 위임(general-purpose / explore / plan / coder), 격리 서브에이전트, 병렬 워커, Swarm으로 긴 작업 분할.
- **Cron & 백그라운드** — `qxt cron start --detach`; 헤드리스로도 자율 가동.
- **Hooks** — `PreToolUse`가 `block`하거나 `args`를 수정（`hooks.allow_edit_args`）。
- **관측성** — `/stats`·`/audit`·`/impact`·`/bench`·`/cost`; 비용을 테이블 위에.
- **crypto** — v2 확실: `cryptography` 있으면 AES-GCM(AEAD), 없으면 HMAC-SHA256 스트림 암호, `open()`은 MAC 누락·변조 시 늘 fail-closed.

---

## 개발 & 계약

```bash
python -m pytest tests/ -q        # 2000+ 테스트
python -m mypy qingxiaotuan       # 타입 게이트
qxt --print run "안녕."            # 스모크
```

- **다국어 문서 규율**: `README_zh-CN.md`가 권위 있는 마스터. 각 언어는 "생생하고, 드리프트 없이" 로컬라이즈, **기계 번역 금지**.
- **규모**: 약 414 `.py` / 약 7.7만 줄 / 32 패키지 / 플러그인 15+.
- **버전**: `v0.2.014`（0.x/Beta）; 파괴적 변경은 마이너 버전에서 사전 공지 + 마이그레이션 힌트.
- **딥다이브**: 9대 엔진 시그니처와 새 도구 워크스루는 `README_zh-CN.md` 부록.

---

## License & 관련

- **License**: MIT（자유 사용·수정·배포, 저작권 표시 유지）
- **필독**: [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)

> "박스깨고 나오자마자 특정 벤더에 묶이고 싶다"는 사람들은 다른 거 고르세요. **내 모델로 돌리고, 통제권을 갖고, 사고 났을 때 되돌릴 수 있는**——열쇠는 여기.