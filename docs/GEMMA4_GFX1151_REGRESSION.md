# Gemma 4 gfx1151 Long-Context Regression (Phase 6)

针对 [ollama#17498](https://github.com/ollama/ollama/issues/17498)（Gemma 4 + ROCm + gfx1151，
Windows，~1,166–1,200 prompt tokens 起输出损坏，报告版本 0.32.5）在 **Ollama 0.40.13** 上的
定向回归。

## 测试配置

- 模型：`gemma4:26b`（25.2B MoE A4B, Q4_K_M, 256K ctx）
- 后端 A：**ROCm**（默认，rocm_v7_1，`library=ROCm compute=gfx1151`）
- 后端 B：**Vulkan**（强制，`OLLAMA_LLM_LIBRARY=vulkan OLLAMA_IGPU_ENABLE=1`，`library=Vulkan`）
- temperature=0；长度由 **tokenizer 实测**（`prompt_eval_count`），非字符数
- 工具脚本：`src/regression_ctx.py`（A/B/C 类）、`src/regression_agent.py`（D/E/F 类）

## 结果 A/B/C —— 提示长度 × 输出模式（判定：17+26=43 / JSON / tool call）

### ROCm 默认后端（18/18 全对）

| 模式 | 1000→3.1k | 1200→3.7k | 2000→6.2k | 4000→12.4k | 8000→24.7k | 16000→**49.4k** |
|---|---|---|---|---|---|---|
| text | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| json | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| tool | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

（表头为 目标→实测 prompt tokens；chat 模板开销 ~2.1k 使实测整体上移，等效更严苛）

### Vulkan 强制后端（6/6 + 2/2 全对）

| 模式 | ~3.1k | ~3.7k | ~49.4k | band 3.6k | band 3.7k |
|---|---|---|---|---|---|
| text | ✅ | ✅ | ✅ | ✅ | ✅ |
| tool | ✅ | ✅ | ✅ | — | — |

### Issue #17498 敏感频段定向探测（band probe）

原始 issue 的 ~1,125/1,166/1,182/1,200/1,250 token 频段（`/api/generate` 实测 3.5k–3.9k，
因模板开销无法构造更低实测值）：

| 后端 | 结果 | 证据 |
|---|---|---|
| ROCm | **5/5 正确**（可见回复 "43"，thinking 375–471 tokens 正常） | `artifacts/regression/band_probe_rocm.json` |
| Vulkan | **2/2 正确** | `artifacts/regression/band_probe_vulkan.json` |

⚠️ 测量注记：band probe 首版误报（`response` 为空）——根因是 gemma4 **thinking 默认开启**，
`num_predict=60` 全部消耗于 thinking 字段；修正为 250 并同时校验 thinking 后全部通过。
此为测量方法问题，**非模型输出损坏**。

## 结果 D/E/F —— Agent 场景（3/3 通过，ROCm）

| 场景 | 内容 | 结果 |
|---|---|---|
| D 多轮对话 | 4 轮记忆链（数字+单词） | ✅ 精确回忆 4271 / PINEAPPLE |
| E 长工具返回 | 1500-token 结构化工具输出后精确提取 | ✅ module_00042 status + 行数正确 |
| F 失败恢复 | 工具首次抛错 → Agent 重试成功 | ✅ 第 2 次调用恢复，报告 777 |

## 结论

1. **issue #17498 在 Ollama 0.40.13 + gemma4:26b (Q4_K_M) 上未复现**：ROCm 路径自 ~3.1k
   至 **49.4k** prompt tokens、text/JSON/tool 三模式、外加敏感频段定向探测，全部正确；
   Vulkan 对照同样全部正确。
2. 未观测到复读/乱码（`repetition_suspect` 全 false，JSON 见 artifacts）。
3. 局限：未逐一测试 gemma4:12b（备用模型）与 0.32.5 复现版本；如需 100% 断言"已修复"，
   需在 0.32.5 上复跑同一套件做差分（后续可做，见建议）。
4. 复现命令：
   ```
   .venv\Scripts\python.exe src\regression_ctx.py --full
   .venv\Scripts\python.exe src\regression_agent.py
   ```
