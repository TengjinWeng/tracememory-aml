# 模型接入方式与指定模型开发额度

开发阶段可以先复用服务器已登录的 Codex CLI，并部署开源 Embedding；不必先为下表充值。
Codex 的真实调用可用性与剩余额度尚未验证。本地 Embedding 实验在 `experiments/local_embedding/`，
使用 Qwen3-Embedding-0.6B；模型不同，不冒充 text-embedding-v4，也未切换正式参评接口。
本地测试使用自建数据，不能把它的结果当作指定模型结果或官方成绩。

下表适用于指定模型路线的小规模开发准备，不是完整官方评测的费用承诺。
价格核对日期：2026-10-29。官网模型要求位于 https://agentmemoryleaderboard.ai/zh-cn/docs#doc-overview 。

| 指定模型路线所需项 | 用途 | 需要提供什么 | 建议初始开发预算 |
| --- | --- | --- | --- |
| gpt-4o-mini，支持文字与图片输入 | 视觉描述、OCR、事实与实体抽取、图片查询理解 | API Key、实际 Base URL、可调用的准确模型 ID | 约 5 美元，可按账户情况少量试用后再补 |
| 阿里云百炼 text-embedding-v4 | 记忆分块和查询向量，1024 维 | API Key、对应地域及 Base URL/Workspace ID | 先核对免费额度；不足时预备 10 元 |

Reranker API 暂时不需要购买；当前候选版用 RRF 融合、实体二跳扩展和时间偏好排序。
目前也不需要购买 GPU、训练额度、额外音频/视频模型。

## 官方入口与价格

OpenAI API：https://platform.openai.com/api-keys

官方模型页：https://developers.openai.com/api/docs/models/gpt-4o-mini

该页标示文本输入 $0.15/百万 Token、输出 $0.60/百万 Token；图像也产生输入 Token，
费用随图像处理方式变化。当前模型页已标记 Deprecated，因此应先验证本账户/渠道
确实仍可调用指定模型（含图片），再扩大充值。程序不会替换成其他模型。
ChatGPT 订阅和本任务所需 API 额度不是同一件事，不能把聊天订阅直接当作已配好的 API Key。

阿里云百炼：https://bailian.console.aliyun.com/

获取 Key：https://help.aliyun.com/zh/model-studio/get-api-key

模型信息与价格：https://help.aliyun.com/zh/model-studio/text-embedding-v4

同步接口：https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/

北京/新加坡页面标示标准输入 0.5 元/百万 Token，国际部署 0.514 元/百万 Token。
免费额度以账户实际状态为准。需区分地域，不同地域的 Key/业务空间与 URL 要对应。
当前北京兼容接口 Base URL 形式是：
`https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
请直接提供控制台显示的实际地址，不要把占位符作为地址。

## 私密配置位置

桌面 `AgentMemory参赛/secrets/model-apis.env`，权限 0600，已被 Git 忽略。

需要填写：OPENAI_API_KEY、OPENAI_BASE_URL、DASHSCOPE_API_KEY、DASHSCOPE_BASE_URL。
保留 OPENAI_MODEL=gpt-4o-mini、EMBEDDING_MODEL=text-embedding-v4。
若用第三方服务商，还需说明服务商、实际模型映射与价格，以便如实披露并核对规则。

开发初始上限为 20 次 LLM 请求、100 次 Embedding 请求，计数持久化在候选版数据库中。
失败或返回状态不确定的请求也计数，不自动重试，避免意外追加费用。
上限按请求数而非金额计算，不等同于供应商硬性费用封顶；API 账户余额和用量也需查看。
缓存按完整 user_id 隔离，不跨用户或评测运行共享。

默认 `python3 scripts/check_model_apis.py` 只检查字段，不发请求。
显式加 `--call` 才做一张自制小图和一句文本的可用性测试，最多请求两个模型各一次。
尚未拿到真实密钥，本轮没有实际调用或产生模型费用；真实质量与费用仍待测试。
