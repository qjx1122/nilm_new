"""深度序列模型（M2，技术方案 §6.1）：LSTM / 1D-CNN / Transformer 时序回归。

实现要点（与占位说明一致）：
1. 适配器内部构造滑动窗口张量（左侧复制填充，保证逐点输出），对外仍暴露
   BaseModel.fit/predict 的 (n, f) → (n, k) 矩阵接口——评估层对模型族无感知；
2. 训练细节（窗口长度/轮数/批大小/学习率/早停耐心/设备/随机种子）经 params 传入；
3. 持久化沿用 BaseModel 整对象 pickle（torch 模块可 pickle），推理路径零改动；
4. 依赖 torch 写入 requirements-ml.txt，惰性导入——未安装时仅在实例化时报错。

窗口语义（Seq2Point 逐点版）：预测时刻 t 的输入为 [t-L+1, t] 的特征序列
（序列头部不足 L 时复制首行填充），标签为 t 时刻分路功率——推理时每个
时间点都有输出，与扁平模型输出对齐。
"""

from __future__ import annotations

import numpy as np

from nilm.common.logging import get_logger
from nilm.models.base import BaseModel
from nilm.models.registry import MODEL_REGISTRY

log = get_logger("models.seq")


def _padded_windows(X: np.ndarray, window: int) -> np.ndarray:
    """(n, f) → (n, window, f) 滑窗视图；头部复制首行填充（零拷贝 stride 视图）。"""
    pad = np.repeat(X[:1], window - 1, axis=0)
    Xp = np.concatenate([pad, X], axis=0)
    win = np.lib.stride_tricks.sliding_window_view(Xp, window, axis=0)
    return np.swapaxes(win, 1, 2)  # (n, window, f)


class _SeqTorchModel(BaseModel):
    """torch 序列回归适配器基类：滑窗构造 / 训练循环 / 早停 / 批推理。"""

    name = "seq_base"

    def __init__(self, window: int = 96, epochs: int = 60, batch_size: int = 256,
                 lr: float = 1e-3, patience: int = 8, device: str = "cpu",
                 random_state: int = 42, **params) -> None:
        super().__init__(window=window, epochs=epochs, batch_size=batch_size,
                         lr=lr, patience=patience, device=device,
                         random_state=random_state, **params)

    # 子类实现：返回 nn.Module，输入 (B, L, F) 输出 (B, K)
    def _build_net(self, n_feat: int, n_out: int):
        raise NotImplementedError

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        import torch

        seed = int(self.params["random_state"])
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)   # 打乱用独立 RNG（同种子可复现）
        window = int(self.params["window"])
        device = torch.device(self.params["device"])
        self._n_feat, self._n_out = X.shape[1], y.shape[1]
        self._net = self._build_net(self._n_feat, self._n_out).to(device)

        Xw = _padded_windows(np.asarray(X, np.float32), window)
        yt = np.asarray(y, np.float32)
        has_val = X_val is not None and y_val is not None and len(X_val) > 0
        if has_val:
            Xw_val = _padded_windows(np.asarray(X_val, np.float32), window)
            yv = torch.from_numpy(np.asarray(y_val, np.float32)).to(device)

        opt = torch.optim.Adam(self._net.parameters(), lr=float(self.params["lr"]))
        loss_fn = torch.nn.MSELoss()
        bs = int(self.params["batch_size"])
        best_val, best_state, bad = float("inf"), None, 0

        for epoch in range(int(self.params["epochs"])):
            self._net.train()
            perm = rng.permutation(len(Xw))
            total = 0.0
            for s in range(0, len(perm), bs):
                idx = perm[s:s + bs]
                xb = torch.from_numpy(np.ascontiguousarray(Xw[idx])).to(device)
                yb = torch.from_numpy(yt[idx]).to(device)
                opt.zero_grad()
                loss = loss_fn(self._net(xb), yb)
                loss.backward()
                opt.step()
                total += float(loss.detach()) * len(idx)
            train_loss = total / len(Xw)

            if has_val:  # 早停：验证损失不再下降 patience 轮则回滚最优权重
                self._net.eval()
                with torch.no_grad():
                    pv = self._predict_windows(Xw_val, device)
                    val_loss = float(loss_fn(pv, yv))
                if val_loss < best_val - 1e-9:
                    best_val, bad = val_loss, 0
                    best_state = {k: v.detach().clone()
                                  for k, v in self._net.state_dict().items()}
                else:
                    bad += 1
                    if bad >= int(self.params["patience"]):
                        log.info("[%s] 早停于 epoch %d（best val_loss=%.6f）",
                                 self.name, epoch + 1, best_val)
                        break
            log.debug("[%s] epoch %d train_loss=%.6f", self.name, epoch + 1, train_loss)

        if best_state is not None:
            self._net.load_state_dict(best_state)
        self._net.eval()

    def _predict_windows(self, Xw: np.ndarray, device):
        """批量前向（返回 torch.Tensor，调用方决定 detach/numpy）。"""
        import torch

        outs = []
        bs = int(self.params["batch_size"])
        for s in range(0, len(Xw), bs):
            xb = torch.from_numpy(np.ascontiguousarray(Xw[s:s + bs])).to(device)
            outs.append(self._net(xb))
        return torch.cat(outs, dim=0)

    def predict(self, X) -> np.ndarray:
        import torch

        device = torch.device(self.params["device"])
        Xw = _padded_windows(np.asarray(X, np.float32), int(self.params["window"]))
        self._net.eval()
        with torch.no_grad():
            out = self._predict_windows(Xw, device)
        return out.cpu().numpy().reshape(len(X), self._n_out)

    # ---- 持久化：nn.Module 为局部类不可直接 pickle——只序列化权重 state_dict，
    #      反序列化时经 _build_net 重建结构再载入权重（BaseModel.load 路径不变）。
    def __getstate__(self) -> dict:
        state = {k: v for k, v in self.__dict__.items() if k != "_net"}
        if getattr(self, "_net", None) is not None:
            state["_net_state_dict"] = {k: v.cpu() for k, v in
                                        self._net.state_dict().items()}
        return state

    def __setstate__(self, state: dict) -> None:
        net_state = state.pop("_net_state_dict", None)
        self.__dict__.update(state)
        if net_state is not None:
            self._net = self._build_net(self._n_feat, self._n_out)
            self._net.load_state_dict(net_state)
            self._net.eval()


@MODEL_REGISTRY.register("lstm")
class LSTMDisaggregator(_SeqTorchModel):
    """LSTM 时序回归：LSTM 末隐状态 → 全连接头。"""

    name = "lstm"

    def __init__(self, hidden_size: int = 64, num_layers: int = 1,
                 dropout: float = 0.0, **params) -> None:
        super().__init__(hidden_size=hidden_size, num_layers=num_layers,
                         dropout=dropout, **params)

    def _build_net(self, n_feat: int, n_out: int):
        import torch.nn as nn

        hidden = int(self.params["hidden_size"])
        layers = int(self.params["num_layers"])

        class Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.lstm = nn.LSTM(n_feat, hidden, num_layers=layers,
                                    batch_first=True,
                                    dropout=float(0.0 if layers == 1 else 0.1))
                self.head = nn.Linear(hidden, n_out)

            def forward(self, x):  # x: (B, L, F)
                out, _ = self.lstm(x)
                return self.head(out[:, -1])

        return Net()


@MODEL_REGISTRY.register("cnn1d")
class CNN1DDisaggregator(_SeqTorchModel):
    """1D-CNN 时序回归（Seq2Point 风格）：卷积堆叠 → 全局平均池化 → 全连接头。"""

    name = "cnn1d"

    def __init__(self, channels: int = 32, kernel_size: int = 5,
                 num_blocks: int = 3, **params) -> None:
        super().__init__(channels=channels, kernel_size=kernel_size,
                         num_blocks=num_blocks, **params)

    def _build_net(self, n_feat: int, n_out: int):
        import torch.nn as nn

        ch = int(self.params["channels"])
        k = int(self.params["kernel_size"])
        blocks = int(self.params["num_blocks"])

        class Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                convs, c_in = [], n_feat
                for _ in range(blocks):
                    convs += [nn.Conv1d(c_in, ch, k, padding=k // 2), nn.ReLU()]
                    c_in = ch
                self.conv = nn.Sequential(*convs)
                self.head = nn.Linear(ch, n_out)

            def forward(self, x):  # x: (B, L, F) → Conv1d 期望 (B, F, L)
                h = self.conv(x.transpose(1, 2))
                return self.head(h.mean(dim=2))

        return Net()


@MODEL_REGISTRY.register("transformer")
class TransformerDisaggregator(_SeqTorchModel):
    """Transformer 时序回归：线性投影 + 位置编码 → Encoder → 末 token → 全连接头。"""

    name = "transformer"

    def __init__(self, d_model: int = 64, nhead: int = 4, num_layers: int = 2,
                 dim_feedforward: int = 128, dropout: float = 0.1, **params) -> None:
        super().__init__(d_model=d_model, nhead=nhead, num_layers=num_layers,
                         dim_feedforward=dim_feedforward, dropout=dropout, **params)

    def _build_net(self, n_feat: int, n_out: int):
        import torch
        import torch.nn as nn

        d = int(self.params["d_model"])
        window = int(self.params["window"])

        class Net(nn.Module):
            def __init__(self, p) -> None:
                super().__init__()
                self.proj = nn.Linear(n_feat, d)
                self.pos = nn.Parameter(torch.zeros(1, window, d))
                layer = nn.TransformerEncoderLayer(
                    d_model=d, nhead=int(p["nhead"]),
                    dim_feedforward=int(p["dim_feedforward"]),
                    dropout=float(p["dropout"]), batch_first=True)
                self.enc = nn.TransformerEncoder(layer, int(p["num_layers"]))
                self.head = nn.Linear(d, n_out)

            def forward(self, x):  # x: (B, L, F)
                h = self.proj(x) + self.pos[:, :x.shape[1]]
                return self.head(self.enc(h)[:, -1])

        return Net(self.params)
