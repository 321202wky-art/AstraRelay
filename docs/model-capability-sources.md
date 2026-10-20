# 模型能力定位依据

本表用于审计 `model_capabilities.py` 中的公开能力标签。只记录模型原厂、官方 API 文档或原厂开源仓库；渠道目录自身的分类不作为能力依据。

| 模型家族 | 当前定位 | 官方依据 |
| --- | --- | --- |
| Claude | 文本与图片输入的多模态模型 | https://platform.claude.com/docs/en/about-claude/models/overview |
| Gemma 3 / Gemma 4 | 文本与图片输入的多模态模型 | https://ai.google.dev/gemma/docs/get_started |
| OpenAI o3 / Deep Research | 文本与图片输入的推理/研究模型 | https://developers.openai.com/api/docs/models/o3 、https://developers.openai.com/api/docs/models/o3-deep-research 、https://developers.openai.com/api/docs/models/o4-mini-deep-research |
| Grok 3 / 4 旧别名 | 原型号为文本模型；退役别名按官方迁移规则处理 | https://docs.x.ai/developers/migration/may-15-retirement 、https://docs.x.ai/developers/rest-api-reference/management/auth |
| Grok 4.5 | 支持图片理解的多模态模型 | https://docs.x.ai/developers/model-capabilities/images/understanding |
| GLM 非 V 系列 | 文本生成、推理与代码模型 | https://docs.bigmodel.cn/cn/guide/models/text/glm-4.7 |
| MiniMax M2 系列 | 文本、推理、代码与 Agent 模型 | https://platform.minimaxi.com/docs/guides/text-generation |
| MiniMax M3 | 支持图片和视频输入的多模态模型 | https://www.minimaxi.com/models/text/m3 |
| Ministral 3 | 支持视觉输入的多模态模型 | https://docs.mistral.ai/models/ |
| Devstral 2 | 文本代码智能体模型 | https://docs.mistral.ai/models/model-cards/devstral-2-25-12 |
| Step 3 / 3.7 | 图片或图片与视频理解模型 | https://platform.stepfun.com/docs/zh/guides/models/overview |
| Step 1X / 2X | 图像生成模型 | https://platform.stepfun.com/docs/zh/guides/models/overview |
| 豆包 Seed 1.6 / 2.x | 多模态理解模型；Code/Evolving 按官方用途标注 | https://www.volcengine.com/product/doubao 、https://developer.volcengine.com/articles/7610285824933445675 |
| Seed OSS 36B | 文本、推理与代码模型 | https://seed.bytedance.com/blog/seed-oss-open-source-models-release |
| MiMo V2 Flash / Pro | 文本推理、代码与 Agent 模型 | https://github.com/XiaomiMiMo/MiMo-V2-Flash 、https://mimo.mi.com/docs/en-US/updates/model |
| MiMo V2 Omni / V2.5 | 图片、视频、音频与文本理解模型 | https://mimo.mi.com/docs/en-US/news/v2.5-news |
| ERNIE 4.5 / X1 | 文本或推理模型；VL 型号单独存在 | https://cloud.baidu.com/doc/qianfan/s/Jmh4sttph |
| ERNIE 5.0 | 文本、图片、视频和音频理解模型 | https://ernie.baidu.com/blog/posts/ernie5.0/ |
| 混元 A13B / T1 / TurboS / HY3 | 文本、推理、代码与 Agent 模型 | https://cloud.tencent.com/document/product/1729/104753 、https://www.tencent.com/tencent-hunyuan-officially-releases-hy3-advancing-agent-capabilities-and-deeper-product-integration/ |
| Qwen-MT-Image | 图片文字翻译专用模型 | https://help.aliyun.com/zh/model-studio/qwen-mt-image-api |
| Qwen 3.5 / 3.6 / 3.7 / 3.8 Token Plan visual families | 原生视觉理解对话模型；目录中的 `qwen3.8-max` 视为文档列出的 `qwen3.8-max` 家族别名 | https://help.aliyun.com/zh/model-studio/add-vision-skill |
| Riverflow 2 | 图像生成与编辑模型 | https://www.riverflow.ai/models/riverflow-2.0 |
| Luma Uni-1 | 图像生成与编辑模型 | https://lumalabs.ai/uni-1 |
| 360GPT2 Pro / O1 | 通用文本与深度推理模型 | https://ai.360.cn/ai/?target=b5 、https://aiplus.360.cn/hyzx/8935.html |

未知的未来型号仍保持 `pending`，只有加入此依据表并补充回归测试后才能标记为 `verified`。
