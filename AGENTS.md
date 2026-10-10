# Agent 協作約定

## 提交資訊與 PR 標題

提交資訊和 Pull Request 標題使用 Conventional Commits 格式：

```text
<type>(<scope>): <subject>
```

常用 `type`：

- `feat`：新增使用者可見能力
- `fix`：修復問題
- `refactor`：重構，不改變外部行為
- `perf`：效能最佳化
- `test`：測試變更
- `docs`：檔案變更
- `chore`：工程和維護性變更
- `build` / `ci`：構建或持續整合變更

約定：

- `scope` 使用受影響的模組，例如 `assistant`、`marketdata`、`frontend`。
- `subject` 使用簡潔中文描述，首字不加大寫要求，不以句號結尾。
- PR 標題應和本次變更的主要使用者價值一致，例如：
  `feat(assistant): 最佳化 Trace 體驗並新增發現機會工具`

## PR 正文

PR 正文至少包含以下部分：

1. `背景`：說明問題和使用者影響。
2. `變更內容`：按功能模組說明實現和行為變化。
3. `驗證`：列出實際執行的測試、構建或檢查命令及結果。
4. `邊界與風險`：說明未覆蓋範圍、相容性和已知限制。
5. `後續計劃`：只記錄確實需要後續處理的事項。

不要把本地原型、未提交檔案或未經驗證的結果寫成已經交付的功能。

## 分支與合併

- 除非使用者明確要求直接推送 `main`，否則在 `codex/` 字首分支上開發並透過 Pull Request 合併。
- Pull Request 預設使用 squash merge。
- 建立或更新 PR 前先執行與變更相關的測試，並執行 `git diff --check`。
