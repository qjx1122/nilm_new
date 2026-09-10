"""编排层（指南 §12/§13）：唯一允许组合各功能模块的层。

- user_config : 用户 JSON 配置契约（键优先级/字段校验）
- user_task   : 单用户训练/推理任务（一个 user_key 一个独立任务）
- batch       : 多用户批量执行（失败隔离/断点续跑/状态表）

单用户与多用户走同一代码路径：单用户 = users=[一个 key] 的批量执行。
"""

from nilm.pipeline.batch import run_batch
from nilm.pipeline.user_config import (list_user_keys, load_time_filter_config,
                                       resolve_user_config)
from nilm.pipeline.user_task import run_user_infer, run_user_train

__all__ = ["run_batch", "run_user_train", "run_user_infer",
           "load_time_filter_config", "list_user_keys", "resolve_user_config"]
