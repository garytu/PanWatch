# Frontend Workspace Packages

- `@panwatch/api`: 統一的 HTTP 請求入口與領域 API（認證、版本、股票等）。
- `@panwatch/base-ui`: 基礎 UI 元件與樣式工具（原 `src/components/ui/*` 已遷移）。
- `@panwatch/biz-ui`: 業務元件與業務複用邏輯（原 `src/components/*` 業務元件已遷移）。

當前前端頁面已統一從 `@panwatch/api` 發起介面請求，避免在頁面中直接呼叫 `fetch`。
