from .best50 import PlayerBest50


class PlayerPC50(PlayerBest50):
    """按「游玩次数（PC）」排的前 50 首：旧曲 35 + 新曲 15，复用 b50 底图与排版。

    分新旧曲的依据与 b50 完全一致（`Song.isnew`，不是 SD/DX 谱面类型），
    所以「PC35 旧曲 + PC15 新曲」和官方 B35/B15 的划分能对上。

    与 b50 的差别只有三处（其余同一套绘制代码，观感一模一样）：
      · 每行右侧数字 = 该谱面的 playCount（不是 Rating）
      · 行内「定数 -> Rating」→「定数 · PCn」
      · 奖杯位汇总 = PC35 + PC15（不再是 = 玩家 Rating）
    """

    def _score_summary(self) -> str:
        return (
            f"PC35: {self.best50.sd_total} + PC15: {self.best50.dx_total}"
            f" = {self.best50.sd_total + self.best50.dx_total}"
        )

    def _row_detail_text(self, ds, info) -> str:
        return f"{ds} · PC{info.rating}"
