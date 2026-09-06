# 版本迭代策略 / Version Iteration Policy

> **⚠️ 必读 / MANDATORY** — 本文件对青奈科技所有开发者、内部人员、PR提交者具有强制约束力。违反本策略的PR将被拒绝。
> **This document is mandatory for all developers, contributors, and PR submitters. PRs violating this policy will be rejected.**

**Language / 语言:** [English](#english) · [简体中文](#简体中文) · [繁體中文](#繁體中文) · [日本語](#日本語) · [한국어](#한국어) · [Español](#español) · [Português](#português) · [Français](#français) · [Deutsch](#deutsch) · [Русский](#русский)

---

## English

### Core Principle: Small Steps, No Skip-Grading

**Version numbers are not medals — they are iteration records.**

We reject the following behaviors:
- ❌ Jumping from 0.2.01 to 0.3.0 after fixing one major bug
- ❌ Jumping from 0.2.01 to 1.0.0 after adding one new module
- ❌ Bumping versions just to "look impressive"

We insist on the following:
- ✅ Each iteration makes incremental changes, max version bump is +0.0.001
- ✅ No matter how impressive a module/feature is, version only increments slightly
- ✅ Small steps, fast iteration, continuous delivery

### Annual Version Locking Rules

**One major version per year, no early jumps.**

| Year | Major Series | Allowed Range |
|------|-------------|---------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Hard constraints:**
- Before Chinese New Year 2027 (~Feb 6, 2027), **no one** may jump to 0.3 or above.
- Each year's major version unlocks only after **Jan 1 of the next year**.

### Version Increment Rules

**Allowed Increments:**
- Current version: `0.2.01`
- Increment unit: `0.0.001`
- I.e.: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Allowed Scenarios:**
1. **Bug fixes** (regardless of severity)
2. **Small features** (no core architecture impact)
3. **Documentation updates** (README, comments, examples)
4. **Dependency upgrades** (patch-level)
5. **Security patches** (even critical ones — only +0.0.001)
6. **Performance optimizations** (no API changes)
7. **Test additions** (no behavior changes)

**Forbidden Scenarios:**
1. Adding a new engine/module → only +0.0.001 allowed
2. Refactoring core architecture → only +0.0.001 allowed
3. Fixing critical security vulnerabilities → only +0.0.001 allowed
4. Introducing a whole new subsystem → only +0.0.001 allowed
5. 10x performance improvement → only +0.0.001 allowed
6. Any "I think we should bump the major version" thought → REJECTED

### PR/Commit Checklist

Each PR must confirm before submission:
- [ ] Version in pyproject.toml updated (only +0.0.001)
- [ ] Version in package.json synced
- [ ] CHANGELOG.md records this change
- [ ] Version does not cross annual major boundary
- [ ] Current year's major series not breached

**Any PR violating the above will be closed immediately without review.**

---

## 简体中文

### 核心原则：小步快跑，拒绝跳号

**版本号不是勋章，是迭代记录。**

我们拒绝以下行为：
- ❌ 修了一个大BUG就把版本号从 0.2.01 跳到 0.3.0
- ❌ 加了一个新模块就从 0.2.01 跳到 1.0.0
- ❌ 为了"看起来NB"而跃迁版本号

我们坚持以下行为：
- ✅ 每次迭代只做增量修改，版本号最多 +0.0.001
- ✅ 无论修复/新增多么NB的模块/能力，版本号都只能微调
- ✅ 小步快跑，持续交付

### 年度版本锁定规则

**每年一个主版本号，不可提前跃迁。**

| 年份 | 主版本系列 | 允许的版本范围 |
|------|-----------|---------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**硬性约束：**
- 2027年中国农历春节（约2027年2月6日）以前，**任何人不得**将版本号跃迁至 0.3 及以上。
- 每年主版本号只能在**次年1月1日**后解锁。

### 版本号递增规则

**允许的递增：**
- 当前版本：`0.2.01`
- 递增单位：`0.0.001`
- 即：`0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**允许递增的场景：**
1. **Bug 修复**（无论严重程度）
2. **新增小功能**（不影响核心架构）
3. **文档更新**（README、注释、示例）
4. **依赖升级**（patch级）
5. **安全补丁**（即使很关键，也只 +0.0.001）
6. **性能优化**（不改变API）
7. **测试补充**（不改变行为）

**禁止跃迁的场景：**
1. 新增一个引擎/模块 → 只允许 +0.0.001
2. 重构核心架构 → 只允许 +0.0.001
3. 修复重大安全漏洞 → 只允许 +0.0.001
4. 引入全新子系统 → 只允许 +0.0.001
5. 性能提升10倍 → 只允许 +0.0.001
6. 任何"我觉得应该升大版本"的想法 → 拒绝


### 正式开源前版本号停滞规则

**目前（正式开源前版本号始终停滞不前：功能继续加，版本号不变，直到该条信息被从文档中删除，才可继续推进版本号：避免反复修改版本号）**

### PR/提交检查清单

每个 PR 提交前必须确认：
- [ ] 版本号在 pyproject.toml 中已更新（且只 +0.0.001）
- [ ] 版本号在 package.json 中已同步更新
- [ ] CHANGELOG.md 已记录本次变更
- [ ] 版本号未跨越年度主版本边界
- [ ] 当前年份的主版本系列未被突破

**违反以上任何一条的PR将被直接关闭，不进入评审流程。**

---

## 繁體中文

### 核心原則：小步快跑，拒絕跳號

**版本號不是勳章，是迭代記錄。**

我們拒絕以下行為：
- ❌ 修了一個大BUG就把版本號從 0.2.01 跳到 0.3.0
- ❌ 加了一個新模組就從 0.2.01 跳到 1.0.0
- ❌ 為了"看起來NB"而躍遷版本號

我們堅持以下行為：
- ✅ 每次迭代只做增量修改，版本號最多 +0.0.001
- ✅ 無論修復/新增多麼NB的模組/能力，版本號都只能微調
- ✅ 小步快跑，持續交付

### 年度版本鎖定規則

**每年一個主版本號，不可提前躍遷。**

| 年份 | 主版本系列 | 允許的版本範圍 |
|------|-----------|---------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**硬性約束：**
- 2027年中國農曆春節（約2027年2月6日）以前，**任何人不得**將版本號躍遷至 0.3 及以上。
- 每年主版本號只能在**次年1月1日**後解鎖。

### 版本號遞增規則

**允許的遞增：**
- 目前版本：`0.2.01`
- 遞增單位：`0.0.001`
- 即：`0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**允許遞增的場景：**
1. **Bug 修復**（無論嚴重程度）
2. **新增小功能**（不影響核心架構）
3. **文件更新**（README、註釋、示例）
4. **依賴升級**（patch級）
5. **安全補丁**（即使很關鍵，也只 +0.0.001）
6. **效能優化**（不改變API）
7. **測試補充**（不改變行為）

**禁止躍遷的場景：**
1. 新增一個引擎/模組 → 只允許 +0.0.001
2. 重構核心架構 → 只允許 +0.0.001
3. 修復重大安全漏洞 → 只允許 +0.0.001
4. 引入全新子系統 → 只允許 +0.0.001
5. 效能提升10倍 → 只允許 +0.0.001
6. 任何"我覺得應該升大版本"的想法 → 拒絕

### PR/提交檢查清單

每個 PR 提交前必須確認：
- [ ] 版本號在 pyproject.toml 中已更新（且只 +0.0.001）
- [ ] 版本號在 package.json 中已同步更新
- [ ] CHANGELOG.md 已記錄本次變更
- [ ] 版本號未跨越年度主版本邊界
- [ ] 當前年份的主版本系列未被突破

**違反以上任何一條的PR將被直接關閉，不進入評審流程。**

---

## 日本語

### 核心理念：小刻みで、跳番なし

**バージョン番号は勲章ではなく、反復の記録です。**

私たちが拒否する行為：
- ❌ 大きなBUGを修正しただけで 0.2.01 → 0.3.0 にジャンプ
- ❌ 新しいモジュールを追加しただけで 0.2.01 → 1.0.0 にジャンプ
- ❌ 「見た目がすごいから」という理由でバージョンを上げる

私たちが堅持する行為：
- ✅ 各反復は増分変更のみ、最大バージョンアップは +0.0.001
- ✅ どんなにすごいモジュール/機能でも、バージョンは微調整のみ
- ✅ 小さな一歩、速い反復、継続的デリバリー

### 年度バージョンロック規則

**1年に1メジャーバージョン、早期ジャンプ禁止。**

| 年 | メジャー系列 | 許可範囲 |
|------|------------|----------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**ハード制約：**
- 2027年中国の旧正月（約2027年2月6日）以前、**誰も** 0.3 以上にジャンプしてはならない。
- 各年のメジャーバージョンは**翌年1月1日**以降のみ解除。

### バージョン増分規則

**許可される増分：**
- 現在のバージョン：`0.2.01`
- 増分単位：`0.0.001`
- 例：`0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**許可されるシナリオ：**
1. **Bug修正**（深刻度に関わらず）
2. **小機能追加**（コアアーキテクチャに影響なし）
3. **ドキュメント更新**（README、コメント、例）
4. **依存関係アップグレード**（パッチレベル）
5. **セキュリティパッチ**（重大でも +0.0.001 のみ）
6. **性能最適化**（API変更なし）
7. **テスト追加**（挙動変更なし）

**禁止されるシナリオ：**
1. 新しいエンジン/モジュール追加 → +0.0.001 のみ
2. コアアーキテクチャのリファクタリング → +0.0.001 のみ
3. 重大セキュリティ脆弱性の修正 → +0.0.001 のみ
4. 全く新しいサブシステム導入 → +0.0.001 のみ
5. 10倍の性能向上 → +0.0.001 のみ
6. 「メジャーバージョンを上げるべき」という考え → 拒否

### PR/コミットチェックリスト

各PRは提出前に以下を確認：
- [ ] pyproject.toml のバージョン更新（+0.0.001 のみ）
- [ ] package.json のバージョン同期
- [ ] CHANGELOG.md に変更記録
- [ ] 年度メジャー境界を超えていない
- [ ] 今年のメジャー系列を突破していない

**上記に違反するPRは即座に閉じられ、レビューに入りません。**

---

## 한국어

### 핵심 원칙: 작은 걸음, 점프 없이

**버전 번호는 훈장이 아니라 반복 기록입니다.**

우리가 거부하는 행동:
- ❌ 큰 BUG를 고쳤다고 0.2.01 → 0.3.0으로 점프
- ❌ 새 모듈을 추가했다고 0.2.01 → 1.0.0으로 점프
- ❌ "멋있어 보이려고" 버전을 올림

우리가 고수하는 행동:
- ✅ 각 반복은 증분 변경만, 최대 버전 업은 +0.0.001
- ✅ 아무리 대단한 모듈/기능이어도 버전은 미세 조정만
- ✅ 작은 걸음, 빠른 반복, 지속적 전달

### 연도별 버전 잠금 규칙

**1년에 1개 메이저 버전, 조기 점프 금지.**

| 연도 | 메이저 시리즈 | 허용 범위 |
|------|-------------|----------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**하드 제약:**
- 2027년 중국 음력 설날(약 2027년 2월 6일) 이전, **누구도** 0.3 이상으로 점프 금지.
- 각 연도의 메이저 버전은 **다음 해 1월 1일** 이후에만 해제.

### 버전 증분 규칙

**허용되는 증분:**
- 현재 버전: `0.2.01`
- 증분 단위: `0.0.001`
- 예: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**허용되는 시나리오:**
1. **Bug 수정** (심각도와 무관)
2. **소규모 기능** (코어 아키텍처 영향 없음)
3. **문서 업데이트** (README, 주석, 예제)
4. **의존성 업그레이드** (패치 레벨)
5. **보안 패치** (중요해도 +0.0.001만)
6. **성능 최적화** (API 변경 없음)
7. **테스트 추가** (동작 변경 없음)

**금지되는 시나리오:**
1. 새 엔진/모듈 추가 → +0.0.001만 허용
2. 코어 아키텍처 리팩토링 → +0.0.001만 허용
3. 중대 보안 취약점 수정 → +0.0.001만 허용
4. 완전히 새로운 서브시스템 도입 → +0.0.001만 허용
5. 10배 성능 향상 → +0.0.001만 허용
6. "메이저 버전을 올려야 한다"는 생각 → 거부

### PR/커밋 체크리스트

각 PR은 제출 전에 확인:
- [ ] pyproject.toml 버전 업데이트 (+0.0.001만)
- [ ] package.json 버전 동기화
- [ ] CHANGELOG.md 변경 기록
- [ ] 연도 메이저 경계를 넘지 않음
- [ ] 올해 메이저 시리즈를 돌파하지 않음

**위 사항을 위반한 PR은 즉시 닫히고 리뷰에 들어가지 않습니다.**

---

## Español

### Principio Central: Pasos Pequeños, Sin Saltos

**Los números de versión no son medallas — son registros de iteración.**

Rechazamos los siguientes comportamientos:
- ❌ Saltar de 0.2.01 a 0.3.0 después de arreglar un bug grande
- ❌ Saltar de 0.2.01 a 1.0.0 después de agregar un módulo
- ❌ Subir versiones solo para "verse impresionante"

Insistimos en lo siguiente:
- ✅ Cada iteración hace cambios incrementales, máximo +0.0.001
- ✅ No importa cuán impresionante sea un módulo/función, la versión solo incrementa ligeramente
- ✅ Pasos pequeños, iteración rápida, entrega continua

### Reglas de Bloqueo Anual de Versiones

**Una versión mayor por año, sin saltos tempranos.**

| Año | Serie Mayor | Rango Permitido |
|-----|------------|----------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Restricciones duras:**
- Antes del Año Nuevo Chino 2027 (~6 de febrero de 2027), **nadie** puede saltar a 0.3 o superior.
- Cada versión mayor anual se desbloquea solo después del **1 de enero del siguiente año**.

### Reglas de Incremento de Versión

**Incrementos permitidos:**
- Versión actual: `0.2.01`
- Unidad de incremento: `0.0.001`
- Ej: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Escenarios permitidos:**
1. **Corrección de bugs** (sin importar gravedad)
2. **Pequeñas funciones** (sin impacto en arquitectura)
3. **Actualizaciones de documentación** (README, comentarios, ejemplos)
4. **Actualización de dependencias** (nivel patch)
5. **Parches de seguridad** (incluso críticos — solo +0.0.001)
6. **Optimización de rendimiento** (sin cambios de API)
7. **Adición de pruebas** (sin cambios de comportamiento)

**Escenarios prohibidos:**
1. Agregar un motor/módulo → solo +0.0.001 permitido
2. Refactorizar arquitectura central → solo +0.0.001 permitido
3. Corregir vulnerabilidades críticas → solo +0.0.001 permitido
4. Introducir un subsistema nuevo → solo +0.0.001 permitido
5. Mejora de rendimiento 10x → solo +0.0.001 permitido
6. Cualquier "creo que deberíamos subir la versión mayor" → RECHAZADO

### Checklist de PR/Commit

Cada PR debe confirmar antes del envío:
- [ ] Versión en pyproject.toml actualizada (solo +0.0.001)
- [ ] Versión en package.json sincronizada
- [ ] CHANGELOG.md registra el cambio
- [ ] La versión no cruza el límite mayor anual
- [ ] La serie mayor del año actual no se ha violado

**Cualquier PR que viole lo anterior se cerrará inmediatamente sin revisión.**

---

## Português

### Princípio Central: Passos Pequenos, Sem Saltos

**Números de versão não são medalhas — são registros de iteração.**

Rejeitamos os seguintes comportamentos:
- ❌ Pular de 0.2.01 para 0.3.0 após corrigir um bug grande
- ❌ Pular de 0.2.01 para 1.0.0 após adicionar um módulo
- ❌ Aumentar versões apenas para "parecer impressionante"

Insistimos no seguinte:
- ✅ Cada iteração faz mudanças incrementais, máximo +0.0.001
- ✅ Não importa quão impressionante seja um módulo/função, a versão só incrementa levemente
- ✅ Passos pequenos, iteração rápida, entrega contínua

### Regras de Bloqueio Anual de Versões

**Uma versão principal por ano, sem saltos antecipados.**

| Ano | Série Principal | Faixa Permitida |
|-----|----------------|----------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Restrições rígidas:**
- Antes do Ano Novo Chinês 2027 (~6 de fevereiro de 2027), **ninguém** pode pular para 0.3 ou superior.
- Cada versão principal anual só é desbloqueada após **1º de janeiro do ano seguinte**.

### Regras de Incremento de Versão

**Incrementos permitidos:**
- Versão atual: `0.2.01`
- Unidade de incremento: `0.0.001`
- Ex: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Cenários permitidos:**
1. **Correção de bugs** (independente da gravidade)
2. **Pequenos recursos** (sem impacto na arquitetura central)
3. **Atualizações de documentação** (README, comentários, exemplos)
4. **Upgrade de dependências** (nível patch)
5. **Patches de segurança** (mesmo críticos — apenas +0.0.001)
6. **Otimização de desempenho** (sem mudanças de API)
7. **Adição de testes** (sem mudanças de comportamento)

**Cenários proibidos:**
1. Adicionar um motor/módulo → apenas +0.0.001 permitido
2. Refatorar arquitetura central → apenas +0.0.001 permitido
3. Corrigir vulnerabilidades críticas → apenas +0.0.001 permitido
4. Introduzir um novo subsistema → apenas +0.0.001 permitido
5. Melhoria de desempenho 10x → apenas +0.0.001 permitido
6. Qualquer "acho que deveríamos subir a versão principal" → REJEITADO

### Checklist de PR/Commit

Cada PR deve confirmar antes do envio:
- [ ] Versão em pyproject.toml atualizada (apenas +0.0.001)
- [ ] Versão em package.json sincronizada
- [ ] CHANGELOG.md registra a mudança
- [ ] Versão não cruza o limite anual principal
- [ ] Série principal do ano atual não violada

**Qualquer PR que violar o acima será fechado imediatamente sem revisão.**

---

## Français

### Principe Central: Petits Pas, Pas de Sauts

**Les numéros de version ne sont pas des médailles — ce sont des enregistrements d'itération.**

Nous rejetons les comportements suivants :
- ❌ Passer de 0.2.01 à 0.3.0 après avoir corrigé un gros bug
- ❌ Passer de 0.2.01 à 1.0.0 après avoir ajouté un module
- ❌ Augmenter les versions juste pour "avoir l'air impressionnant"

Nous insistons sur ce qui suit :
- ✅ Chaque itération fait des changements incrémentaux, max +0.0.001
- ✅ Peu importe à quel point un module/fonction est impressionnant, la version n'augmente que légèrement
- ✅ Petits pas, itération rapide, livraison continue

### Règles de Verrouillage Annuel des Versions

**Une version majeure par an, pas de sauts prématurés.**

| Année | Série Majeure | Plage Autorisée |
|-------|--------------|----------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Contraintes strictes :**
- Avant le Nouvel An Chinois 2027 (~6 février 2027), **personne** ne peut passer à 0.3 ou plus.
- Chaque version majeure annuelle ne se débloque qu'après le **1er janvier de l'année suivante**.

### Règles d'IncRémentation de Version

**Incréments autorisés :**
- Version actuelle : `0.2.01`
- Unité d'incrément : `0.0.001`
- Ex : `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Scénarios autorisés :**
1. **Corrections de bugs** (quelle que soit la gravité)
2. **Petites fonctionnalités** (sans impact sur l'architecture)
3. **Mises à jour de documentation** (README, commentaires, exemples)
4. **Mises à niveau de dépendances** (niveau patch)
5. **Correctifs de sécurité** (même critiques — seulement +0.0.001)
6. **Optimisations de performance** (sans changements d'API)
7. **Ajouts de tests** (sans changements de comportement)

**Scénarios interdits :**
1. Ajouter un moteur/module → seulement +0.0.001 autorisé
2. Refactoriser l'architecture centrale → seulement +0.0.001 autorisé
3. Corriger des vulnérabilités critiques → seulement +0.0.001 autorisé
4. Introduire un nouveau sous-système → seulement +0.0.001 autorisé
5. Amélioration de performance 10x → seulement +0.0.001 autorisé
6. Toute pensée "je pense que nous devrions augmenter la version majeure" → REJETÉ

### Checklist PR/Commit

Chaque PR doit confirmer avant soumission :
- [ ] Version dans pyproject.toml mise à jour (seulement +0.0.001)
- [ ] Version dans package.json synchronisée
- [ ] CHANGELOG.md enregistre le changement
- [ ] Version ne traverse pas la frontière majeure annuelle
- [ ] Série majeure de l'année en cours non violée

**Tout PR violant ce qui précède sera fermé immédiatement sans revue.**

---

## Deutsch

### Kernprinzip: Kleine Schritte, Keine Sprünge

**Versionsnummern sind keine Medaillen — sie sind Iterationsaufzeichnungen.**

Wir lehnen folgende Verhaltensweisen ab:
- ❌ Von 0.2.01 auf 0.3.0 springen nach einem großen Bugfix
- ❌ Von 0.2.01 auf 1.0.0 springen nach einem neuen Modul
- ❌ Versionen nur erhöhen, um "beeindruckend zu wirken"

Wir bestehen auf Folgendem:
- ✅ Jede Iteration macht inkrementelle Änderungen, max. +0.0.001
- ✅ Egal wie beeindruckend ein Modul/Feature ist, die Version erhöht sich nur geringfügig
- ✅ Kleine Schritte, schnelle Iteration, kontinuierliche Lieferung

### Jährliche Versionssperrregeln

**Eine Hauptversion pro Jahr, keine frühen Sprünge.**

| Jahr | Hauptserie | Erlaubter Bereich |
|------|-----------|------------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Harte Einschränkungen:**
- Vor dem chinesischen Neujahr 2027 (~6. Februar 2027) darf **niemand** auf 0.3 oder höher springen.
- Jede jährliche Hauptversion wird erst nach dem **1. Januar des Folgejahres** freigeschaltet.

### Versionsinkrement-Regeln

**Erlaubte Inkremente:**
- Aktuelle Version: `0.2.01`
- Inkrementeinheit: `0.0.001`
- D.h.: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Erlaubte Szenarien:**
1. **Bugfixes** (unabhängig vom Schweregrad)
2. **Kleine Funktionen** (keine Kernarchitektur-Auswirkung)
3. **Dokumentationsupdates** (README, Kommentare, Beispiele)
4. **Abhängigkeits-Upgrades** (Patch-Ebene)
5. **Sicherheitspatches** (selbst kritische — nur +0.0.001)
6. **Performance-Optimierungen** (keine API-Änderungen)
7. **Test-Ergänzungen** (keine Verhaltensänderungen)

**Verbotene Szenarien:**
1. Neues Engine/Modul hinzufügen → nur +0.0.001 erlaubt
2. Kernarchitektur refaktorisieren → nur +0.0.001 erlaubt
3. Kritische Sicherheitslücken beheben → nur +0.0.001 erlaubt
4. Neues Subsystem einführen → nur +0.0.001 erlaubt
5. 10x Performance-Verbesserung → nur +0.0.001 erlaubt
6. Jeder Gedanke "ich denke, wir sollten die Hauptversion erhöhen" → ABGELEHNT

### PR/Commit-Checkliste

Jeder PR muss vor dem Einreichen bestätigen:
- [ ] Version in pyproject.toml aktualisiert (nur +0.0.001)
- [ ] Version in package.json synchronisiert
- [ ] CHANGELOG.md dokumentiert die Änderung
- [ ] Version überschreitet nicht die jährliche Hauptgrenze
- [ ] Hauptserie des aktuellen Jahres nicht verletzt

**Jeder PR, der das oben Genannte verletzt, wird sofort geschlossen, ohne Review.**

---

## Русский

### Основной принцип: Маленькие шаги, без прыжков

**Номера версий — не медали, а записи итераций.**

Мы отвергаем следующее поведение:
- ❌ Прыжок с 0.2.01 на 0.3.0 после исправления крупного бага
- ❌ Прыжок с 0.2.01 на 1.0.0 после добавления модуля
- ❌ Повышение версий только ради "впечатляющего вида"

Мы настаиваем на следующем:
- ✅ Каждая итерация вносит инкрементальные изменения, макс. +0.0.001
- ✅ Неважно, насколько впечатляющий модуль/функция, версия увеличивается незначительно
- ✅ Маленькие шаги, быстрая итерация, непрерывная поставка

### Правила годовой блокировки версий

**Одна мажорная версия в год, без ранних прыжков.**

| Год | Мажорная серия | Допустимый диапазон |
|-----|---------------|---------------------|
| 2026 | 0.2.x | 0.2.01 ~ 0.2.999 |
| 2027 | 0.3.x | 0.3.01 ~ 0.3.999 |
| 2028 | 0.4.x | 0.4.01 ~ 0.4.999 |

**Жёсткие ограничения:**
- До китайского Нового года 2027 (~6 февраля 2027) **никто** не может перейти на 0.3 или выше.
- Каждая годовая мажорная версия разблокируется только после **1 января следующего года**.

### Правила инкрементации версии

**Разрешённые инкременты:**
- Текущая версия: `0.2.01`
- Единица инкремента: `0.0.001`
- Т.е.: `0.2.01` → `0.2.011` → `0.2.012` → ... → `0.2.999`

**Разрешённые сценарии:**
1. **Исправление багов** (независимо от серьёзности)
2. **Малые функции** (без влияния на ядро архитектуры)
3. **Обновление документации** (README, комментарии, примеры)
4. **Обновление зависимостей** (уровень patch)
5. **Патчи безопасности** (даже критические — только +0.0.001)
6. **Оптимизация производительности** (без изменений API)
7. **Добавление тестов** (без изменений поведения)

**Запрещённые сценарии:**
1. Добавление движка/модуля → только +0.0.001 разрешено
2. Рефакторинг ядра архитектуры → только +0.0.001 разрешено
3. Исправление критических уязвимостей → только +0.0.001 разрешено
4. Внедрение новой подсистемы → только +0.0.001 разрешено
5. Улучшение производительности в 10 раз → только +0.0.001 разрешено
6. Любая мысль "я думаю, мы должны поднять мажорную версию" → ОТКЛОНЕНО

### Чек-лист PR/коммита

Каждый PR должен подтвердить перед отправкой:
- [ ] Версия в pyproject.toml обновлена (только +0.0.001)
- [ ] Версия в package.json синхронизирована
- [ ] CHANGELOG.md фиксирует изменение
- [ ] Версия не пересекает годовую мажорную границу
- [ ] Мажорная серия текущего года не нарушена

**Любой PR, нарушающий вышеуказанное, будет немедленно закрыт без рецензирования.**

---

*最后修订 / Last revised: 2026-08-28*
*适用于 / Applies to: Qingxiaotuan Agent CLI 及青奈科技所有Python/Node项目 / and all Qingnai Technology Python/Node projects*