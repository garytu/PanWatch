"""Agent 過程評測集（golden set + 規則斷言 + LLM-as-judge 框架）。

與業務後驗評測（factor_eval 等"評結果"）互補，這裡"評過程"：
工具選擇/引數/有據性/結構化輸出可解析/動作白名單。

- 純規則用例（structured_output 解析）隨 make test 常跑；
- 需要真實模型的 chat 工具迴圈用例透過 make eval 執行（配置見 run_eval.py）。
"""
