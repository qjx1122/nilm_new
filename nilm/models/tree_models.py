"""树模型族（M2，技术方案 §6.1）：RandomForest / XGBoost 多输出回归。

接入约束（与占位说明一致）：
1. 继承 BaseModel，fit/predict 维持 (n, f) → (n, k) 矩阵接口；
   多输出 = 原生 multioutput（RF）或每分路一个回归器（XGB）；
2. ``@MODEL_REGISTRY.register(...)`` 注册，配置驱动实例化；
3. 依赖（scikit-learn / xgboost）写入 requirements-ml.txt，import 惰性——
   未安装时仅在实例化对应模型时报错，不影响核心流程。
"""

from __future__ import annotations

import numpy as np

from nilm.models.base import BaseModel
from nilm.models.registry import MODEL_REGISTRY


@MODEL_REGISTRY.register("random_forest")
class RandomForestDisaggregator(BaseModel):
    """随机森林回归（sklearn 原生 multioutput）。"""

    name = "random_forest"

    def __init__(self, n_estimators: int = 200, max_depth: int | None = None,
                 min_samples_leaf: int = 2, n_jobs: int = -1,
                 random_state: int = 42, **params) -> None:
        super().__init__(n_estimators=n_estimators, max_depth=max_depth,
                         min_samples_leaf=min_samples_leaf, n_jobs=n_jobs,
                         random_state=random_state, **params)

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        from sklearn.ensemble import RandomForestRegressor  # 惰性导入

        self._n_out = y.shape[1]
        self._model = RandomForestRegressor(
            n_estimators=int(self.params["n_estimators"]),
            max_depth=self.params["max_depth"],
            min_samples_leaf=int(self.params["min_samples_leaf"]),
            n_jobs=int(self.params["n_jobs"]),
            random_state=int(self.params["random_state"]))
        self._model.fit(X, y if self._n_out > 1 else y.ravel())

    def predict(self, X) -> np.ndarray:
        pred = self._model.predict(X)
        return pred.reshape(len(X), self._n_out)


@MODEL_REGISTRY.register("xgboost")
class XGBoostDisaggregator(BaseModel):
    """XGBoost 回归（每分路一个回归器；有验证集时启用早停）。"""

    name = "xgboost"

    def __init__(self, n_estimators: int = 400, max_depth: int = 6,
                 learning_rate: float = 0.05, subsample: float = 0.8,
                 colsample_bytree: float = 0.8, early_stopping_rounds: int = 30,
                 random_state: int = 42, **params) -> None:
        super().__init__(n_estimators=n_estimators, max_depth=max_depth,
                         learning_rate=learning_rate, subsample=subsample,
                         colsample_bytree=colsample_bytree,
                         early_stopping_rounds=early_stopping_rounds,
                         random_state=random_state, **params)

    def _make_one(self, use_early_stop: bool):
        from xgboost import XGBRegressor  # 惰性导入

        return XGBRegressor(
            n_estimators=int(self.params["n_estimators"]),
            max_depth=int(self.params["max_depth"]),
            learning_rate=float(self.params["learning_rate"]),
            subsample=float(self.params["subsample"]),
            colsample_bytree=float(self.params["colsample_bytree"]),
            random_state=int(self.params["random_state"]),
            early_stopping_rounds=(int(self.params["early_stopping_rounds"])
                                   if use_early_stop else None),
            n_jobs=-1, verbosity=0)

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        self._n_out = y.shape[1]
        has_val = X_val is not None and y_val is not None and len(X_val) > 0
        self._models = []
        for k in range(self._n_out):
            m = self._make_one(use_early_stop=has_val)
            if has_val:
                m.fit(X, y[:, k], eval_set=[(X_val, y_val[:, k])], verbose=False)
            else:
                m.fit(X, y[:, k])
            self._models.append(m)

    def predict(self, X) -> np.ndarray:
        return np.column_stack([m.predict(X) for m in self._models])
